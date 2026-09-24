"""Claim-fenced orchestration with isolated research and parent-only CRM writes."""

from __future__ import annotations

import asyncio
import os
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from functools import partial
from multiprocessing.connection import Connection
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from malg.config import (
    ResearchConfig,
    WorkerConfig,
    get_research_config,
    get_search_config,
    load_settings,
)
from malg.core.agent_tracing import (
    AgentTraceRecorder,
    install_trace_log_handler,
    record_active_event,
)
from malg.core.budget import BudgetExhausted, ResearchBudget, bind_budget
from malg.core.claims import validate_claim_proposal
from malg.core.models.account import AccountData, AccountResearchResult, AccountValidationAssessment
from malg.core.models.campaign import CampaignData
from malg.core.models.icp import ICPData
from malg.core.models.person import PersonData
from malg.core.models.research import ResearchOutcome, ResearchResult
from malg.core.retrieval import RetrievalService
from malg.core.web_search import SearxngSearchClient
from malg.crm.client import TwentyClient, TwentyError, TwentySchemaIncompatible
from malg.crm.contracts import CRMRecord
from malg.crm.identity import deterministic_id, normalize_domain, normalize_linkedin
from malg.crm.publisher import CrmPublisher
from malg.database.jobs import claim_next_job, complete_job, fail_job, renew_claim, require_claim
from malg.database.models import (
    CrmWriteOperation,
    ResearchClaim,
    ResearchJob,
    ResearchSource,
    ResearchStageResult,
    ResearchWorkflow,
    SourceFetch,
)
from malg.database.research import (
    canonical_input_hash,
    create_workflow_for_job,
    persist_stage_result,
)
from malg.database.session import make_engine, make_session_factory
from malg.database.workers import record_worker_heartbeat
from malg.worker.execution import ExecutionConfig, run_supervised


def _aware(value: datetime) -> datetime:
    """Normalize SQLite test timestamps; PostgreSQL already supplies UTC offsets."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _stage_model(
    kind: str, stage: str
) -> type[ResearchOutcome] | type[AccountValidationAssessment]:
    """Select the host-owned checkpoint model for the requested bounded stage."""
    if stage.endswith(".validation"):
        return AccountValidationAssessment
    if kind in {"account", "account_hydration"}:
        return AccountResearchResult
    if kind in {"person", "person_hydration"}:
        return ResearchResult[PersonData]
    if kind == "icp":
        return ResearchResult[ICPData]
    return ResearchResult[CampaignData]


def _stage_budget(
    sessions: sessionmaker[Session], job: ResearchJob, config: ResearchConfig
) -> ResearchBudget:
    """Fence each external request and persist workflow-wide attempt reservations."""
    if job.deadline_at is None:
        raise ValueError("missing workflow deadline")
    limits = {
        "llm": config.max_llm_attempts_per_workflow,
        "search": config.max_search_requests,
        "fetch": config.max_fetch_requests,
    }

    def reserve(kind: str) -> None:
        with sessions.begin() as session:
            current = require_claim(session, job.job_id, job.claim_token or "", datetime.now(UTC))
            workflow = session.get(ResearchWorkflow, current.workflow_id)
            if workflow is None:
                raise ValueError("missing workflow")
            field = f"{kind}_attempts"
            count = getattr(workflow, field)
            if count >= limits[kind]:
                raise BudgetExhausted(f"{kind} workflow budget exhausted")
            setattr(workflow, field, count + 1)

    return ResearchBudget(
        min(
            _aware(job.deadline_at),
            datetime.now(UTC) + timedelta(seconds=config.stage_timeout_seconds),
        ),
        cleanup_reserve_seconds=config.cleanup_reserve_seconds,
        limits={**limits, "llm": config.max_llm_attempts_per_stage},
        reserve=reserve,
    )


def _child_stage(
    database_url: str,
    job_id: str,
    claim_token: str,
    stage_key: str,
    payload: dict[str, Any],
    initialized: Connection,
) -> None:
    """Initialize all stage resources in the spawned child and persist before exit."""
    # Generation has no CRM or API-auth authority; publication stays in the parent.
    for key in tuple(os.environ):
        if key.startswith(("MALG_TWENTY__", "MALG_TWENTY_SCHEMA__", "MALG_AUTH__")):
            os.environ.pop(key)
    asyncio.run(
        _child_stage_async(database_url, job_id, claim_token, stage_key, payload, initialized)
    )


async def _child_stage_async(
    database_url: str,
    job_id: str,
    claim_token: str,
    stage_key: str,
    payload: dict[str, Any],
    initialized: Connection,
) -> None:
    from nooa.config import CodeActConfig
    from nooa.strategies import CodeActStrategy, set_default_strategy

    from malg.core.account_probes import AccountProbeService
    from malg.core.agents.account_research import AccountResearchAgent
    from malg.core.agents.account_validation import AccountValidationAgent
    from malg.core.agents.campaign_research import CampaignResearchAgent
    from malg.core.agents.icp_research import ICPResearchAgent
    from malg.core.agents.person_research import PersonResearchAgent
    from malg.core.browser_support import aclose_browser
    from malg.core.persistent_memory_support import close_persistent_memory

    engine = make_engine(database_url)
    sessions = make_session_factory(engine)
    agent: Any = None
    trace: AgentTraceRecorder | None = None
    reset = None
    reset_budget = None
    budget: ResearchBudget | None = None
    loop = asyncio.get_running_loop()
    stage_task = asyncio.current_task()
    assert stage_task is not None
    # Let cancellation close traces and child-owned transports before the parent
    # escalates to SIGKILL; never persist a research result after termination.
    loop.add_signal_handler(signal.SIGTERM, stage_task.cancel)
    try:
        with sessions.begin() as session:
            job = require_claim(session, job_id, claim_token, datetime.now(UTC))
            workflow = session.get(ResearchWorkflow, job.workflow_id)
            if workflow is None:
                raise ValueError("missing research workflow")
            workflow_id, input_hash, kind = workflow.workflow_id, workflow.input_hash, job.kind
        research_config = get_research_config(load_settings())
        budget = _stage_budget(sessions, job, research_config)
        reset_budget = bind_budget(budget)
        set_default_strategy(
            CodeActStrategy(config=CodeActConfig(max_iterations=research_config.max_iterations))
        )
        scopes = payload["crm_inputs"]
        campaign = (
            CampaignData.model_validate(scopes["campaign"]["data"])
            if "campaign" in scopes
            else None
        )
        icp = ICPData.model_validate(scopes["icp"]["data"]) if "icp" in scopes else None
        if stage_key.endswith(".validation"):
            agent = AccountValidationAgent(recorder=record_active_event)
            method = "validate_account"
        elif kind == "campaign":
            agent, method = CampaignResearchAgent(recorder=record_active_event), "find_campaign"
        elif kind == "icp":
            agent, method = ICPResearchAgent(recorder=record_active_event), "research_one"
        elif kind in {"account", "account_hydration"}:
            agent, method = AccountResearchAgent(recorder=record_active_event), "research_one"
        else:
            agent, method = PersonResearchAgent(recorder=record_active_event), "research_one"
        trace = AgentTraceRecorder.start_run(sessions, job_id, "agent", agent, method)
        trace.attach(agent)
        reset = trace.bind()
        with sessions.begin() as session:
            require_claim(session, job_id, claim_token, datetime.now(UTC))
        initialized.send_bytes(b"initialized")
        if stage_key.endswith(".validation"):
            candidate = AccountResearchResult.model_validate(payload["research"])
            probes = await AccountProbeService().probe_candidate(candidate)
            with sessions.begin() as session:
                require_claim(session, job_id, claim_token, datetime.now(UTC))
            result = await agent.validate_account(campaign, icp, candidate, probes)
        elif kind == "campaign":
            result = await agent.find_campaign()
        elif kind == "icp":
            result = await agent.research_one(campaign, [])
        elif kind in {"account", "account_hydration"}:
            saved = (
                AccountData.model_validate(scopes["account"]["data"])
                if kind == "account_hydration"
                else None
            )
            result = await agent.research_one(
                campaign,
                icp,
                [],
                missing_fields=payload.get("missing_fields"),
                saved_account=saved,
                candidate_hints=payload.get("candidate_hints"),
            )
        else:
            company = scopes["account"]
            saved_person = (
                PersonData.model_validate(scopes["person"]["data"])
                if kind == "person_hydration"
                else None
            )
            result = await agent.research_one(
                UUID(company["id"]),
                company["data"]["name"],
                icp,
                missing_fields=payload.get("missing_fields"),
                saved_person=saved_person,
            )
        result = _stage_model(kind, stage_key).model_validate(result)
        fetches = agent.retrieval.observations
        excerpts = {
            str(excerpt["id"]): str(excerpt["text"])
            for fetch in fetches
            for excerpt in fetch.excerpts
        }
        claims = []
        if isinstance(result, ResearchResult) and result.data is not None:
            for observation in result.observations:
                if observation.field not in type(result.data).model_fields:
                    raise ValueError("research observation references an unknown business field")
                claims.append(validate_claim_proposal(observation, excerpts))
            if result.outcome in {"complete", "partial"} and not claims:
                raise ValueError("research result lacks verified source observations")
        with sessions.begin() as session:
            require_claim(session, job_id, claim_token, datetime.now(UTC))
            for fetch in fetches:
                source_id = str(uuid5(NAMESPACE_URL, fetch.url))
                source = session.get(ResearchSource, source_id)
                if source is None:
                    try:
                        with session.begin_nested():
                            session.add(
                                ResearchSource(
                                    source_id=source_id,
                                    canonical_url=fetch.url,
                                    original_url=fetch.url,
                                )
                            )
                            session.flush()
                    except IntegrityError:
                        source = session.scalar(
                            select(ResearchSource).where(ResearchSource.canonical_url == fetch.url)
                        )
                        if source is None:
                            raise
                        source_id = source.source_id
                session.add(
                    SourceFetch(
                        fetch_id=str(uuid4()),
                        source_id=source_id,
                        workflow_id=workflow_id,
                        fetched_at=datetime.now(UTC),
                        final_url=fetch.final_url,
                        status_code=fetch.status_code,
                        outcome=fetch.outcome,
                        content_type=fetch.content_type,
                        content_hash=fetch.content_hash,
                        excerpts=list(fetch.excerpts),
                        failure_detail=fetch.reason_code,
                    )
                )
            for claim in claims:
                session.add(
                    ResearchClaim(
                        claim_id=claim.claim_id,
                        workflow_id=workflow_id,
                        kind="observation",
                        text=claim.text,
                        excerpt_refs=list(claim.excerpt_ids),
                    )
                )
            outcome = str(result.outcome)
            if stage_key.endswith(".validation"):
                outcome = "complete" if outcome == "accepted" else "needs_review"
            persist_stage_result(
                session,
                job_id=job_id,
                claim_token=claim_token,
                workflow_id=workflow_id,
                stage_key=stage_key,
                input_hash=input_hash,
                outcome=outcome,
                payload=result.model_dump(mode="json"),
                now=datetime.now(UTC),
                unknowns=getattr(result, "unknowns", []),
                source_refs=[claim.claim_id for claim in claims],
            )
        trace.finish_success(result)
    except asyncio.CancelledError as error:
        if trace:
            trace.finish_failure(error)
        raise
    except Exception as error:
        if not isinstance(error, BudgetExhausted) and not (budget and budget.exhausted):
            if trace:
                trace.finish_failure(error)
            raise
        with sessions.begin() as session:
            persist_stage_result(
                session,
                job_id=job_id,
                claim_token=claim_token,
                workflow_id=workflow_id,
                stage_key=stage_key,
                input_hash=input_hash,
                outcome="budget_exhausted",
                payload={"outcome": "budget_exhausted", "unknowns": ["research budget exhausted"]},
                now=datetime.now(UTC),
                unknowns=["research budget exhausted"],
            )
        if trace:
            trace.finish_success({"outcome": "budget_exhausted"})
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        if reset_budget is not None:
            reset_budget()
        if reset is not None:
            reset()
        if trace:
            trace.close()
        if agent is not None:
            await agent._llm.aclose()
            if hasattr(agent, "browser"):
                await aclose_browser(agent.browser)
            close_persistent_memory(agent)
        engine.dispose()


class ResearchWorker:
    """Run seven bounded job kinds with durable research and fenced publication."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        config: WorkerConfig,
        *,
        crm_publisher: CrmPublisher,
        crm_client: TwentyClient,
    ) -> None:
        self.session_factory, self.config = session_factory, config
        self.crm_client, self.crm_publisher = crm_client, crm_publisher
        self.worker_token = str(uuid4())
        self.research_config = get_research_config(load_settings())
        install_trace_log_handler()

    async def heartbeat(self) -> None:
        """Persist local liveness independently of Twenty availability."""
        with self.session_factory.begin() as session:
            record_worker_heartbeat(session, self.worker_token, datetime.now(UTC))

    async def run_once(self) -> bool:
        """Claim one job and cancel its active supervisor as soon as ownership is lost."""
        with self.session_factory.begin() as session:
            job = claim_next_job(
                session,
                self.worker_token,
                datetime.now(UTC),
                self.config.lease_seconds,
                self.config.max_attempts,
            )
        if job is None:
            return False
        trace = AgentTraceRecorder.start_run(
            self.session_factory,
            job.job_id,
            "worker",
            "ResearchWorker",
            "run_once",
            self.worker_token,
        )
        reset = trace.bind()
        execution = asyncio.create_task(self._dispatch(job))
        renewal = asyncio.create_task(self._renew_loop(job, execution))
        try:
            await execution
            trace.finish_success()
        except asyncio.CancelledError:
            trace.finish_failure(asyncio.CancelledError("execution cancelled"))
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            trace.event("claim_lost", {"job_id": job.job_id})
        except (BudgetExhausted, TimeoutError):
            with suppress(ValueError):
                self._complete(job, "budget_exhausted", {"unknowns": ["research budget exhausted"]})
            trace.finish_success({"outcome": "budget_exhausted"})
        except Exception as error:
            trace.finish_failure(error)
            with self.session_factory.begin() as session, suppress(ValueError):
                fail_job(
                    session,
                    job.job_id,
                    job.claim_token or "",
                    self._safe_failure(error),
                    datetime.now(UTC),
                )
        finally:
            if not execution.done():
                execution.cancel()
                with suppress(asyncio.CancelledError):
                    await execution
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal
            reset()
            trace.close()
        return True

    @staticmethod
    def _safe_failure(error: Exception) -> str:
        """Keep upstream or generated payloads and credentials out of public failures."""
        text = str(error)
        return (
            text
            if text
            in {
                "crm_input_changed",
                "crm_record_missing",
                "crm_scope_conflict",
                "crm_schema_incompatible",
                "crm_identity_conflict",
            }
            else "research_execution_failed"
        )

    async def _renew_loop(self, job: ResearchJob, execution: asyncio.Task[None]) -> None:
        try:
            while True:
                await asyncio.sleep(self.config.lease_seconds / 4)
                with self.session_factory.begin() as session:
                    now = datetime.now(UTC)
                    live = renew_claim(
                        session,
                        job.job_id,
                        job.claim_token or "",
                        now,
                        now + timedelta(seconds=self.config.lease_seconds),
                    )
                if not live:
                    execution.cancel()
                    return
        except Exception:
            execution.cancel()
            raise

    def _fence(self, job: ResearchJob) -> None:
        with self.session_factory.begin() as session:
            require_claim(session, job.job_id, job.claim_token or "", datetime.now(UTC))

    async def _dispatch(self, job: ResearchJob) -> None:
        if job.data_origin != "twenty":
            raise ValueError("legacy_local jobs cannot execute")
        self._fence(job)
        observed = await self.crm_client.check_contract()
        if not observed["schema_compatible"]:
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        if (
            job.contract_version != observed["contract_version"]
            or job.contract_hash != observed["contract_hash"]
        ):
            raise ValueError("crm_input_changed")
        await self.crm_publisher.reconcile_pending(job.job_id)
        request = job.request_payload or {"kind": job.kind}
        records = await self.crm_client.read_scope(request)
        self._fence(job)
        inputs = {key: record.targeting_snapshot() for key, record in records.items()}
        payload: dict[str, Any] = {
            "request": request,
            "crm_inputs": inputs,
            "observed_records": {
                key: record.model_dump(mode="json") for key, record in records.items()
            },
        }
        if job.kind.endswith("_hydration"):
            scope = "account" if job.kind == "account_hydration" else "person"
            payload["missing_fields"] = _missing_fields(records[scope])
        if job.kind == "account":
            payload["candidate_hints"] = {
                key: request[key] for key in ("name", "website") if request.get(key)
            }
        with self.session_factory.begin() as session:
            current = require_claim(session, job.job_id, job.claim_token or "", datetime.now(UTC))
            workflow = create_workflow_for_job(
                session,
                current,
                payload,
                datetime.now(UTC)
                + timedelta(seconds=self.research_config.workflow_timeout_seconds),
            )
            if workflow.input_payload.get("crm_inputs") != inputs:
                raise ValueError("crm_input_changed")
            job.workflow_id, job.deadline_at = workflow.workflow_id, workflow.deadline_at
            job.input_hash = workflow.input_hash
            payload = dict(workflow.input_payload)
            if job.kind.endswith("_hydration"):
                observation = {
                    "observed_records": payload["observed_records"],
                    "missing_fields": payload["missing_fields"],
                }
                persist_stage_result(
                    session,
                    job_id=job.job_id,
                    claim_token=job.claim_token or "",
                    workflow_id=workflow.workflow_id,
                    stage_key=f"{job.kind}.observation",
                    input_hash=workflow.input_hash,
                    outcome="complete",
                    payload=observation,
                    now=datetime.now(UTC),
                )
        if job.kind == "discovery":
            await self._discovery(job, records["icp"].data, request.get("limit", 10))
            return
        if job.kind == "account_hydration":
            await self._account_hydration(job, payload)
            return
        if job.kind == "person_hydration":
            await self._person_hydration(job, payload)
            return
        result = await self._research(job, f"{job.kind}.research", payload)
        if result.data is None or result.outcome not in {"complete", "partial"}:
            self._complete(job, result.outcome, result.model_dump(mode="json"))
            return
        validation = None
        if job.kind == "account":
            validation = await self._research(
                job, "account.validation", {**payload, "research": result.model_dump(mode="json")}
            )
        self._fence(job)
        refreshed = await self.crm_client.read_scope(request)
        if {key: record.targeting_snapshot() for key, record in refreshed.items()} != inputs:
            raise ValueError("crm_input_changed")
        refs = []
        token = job.claim_token or ""
        outcome = result.outcome
        if job.kind == "campaign":
            record_id = await self.crm_publisher.publish_campaign(
                job.job_id, result.data, claim_token=token
            )
            refs = [self._ref("malgCampaign", record_id)]
        elif job.kind == "icp":
            record_id = await self.crm_publisher.publish_icp(
                job.job_id, result.data, campaign_id=str(records["campaign"].id), claim_token=token
            )
            refs = [self._ref("malgIcp", record_id)]
        elif job.kind == "account":
            if not isinstance(validation, AccountValidationAssessment):
                raise ValueError("account_validation_missing")
            company_id, membership_id, reason = await self.crm_publisher.publish_account(
                job.job_id, result, validation, icp_id=str(records["icp"].id), claim_token=token
            )
            refs = [
                self._ref(name, identifier)
                for name, identifier in (("company", company_id), ("malgMembership", membership_id))
                if identifier
            ]
            if reason:
                outcome = "needs_review"
        elif job.kind == "person":
            data = result.data
            company_id = str(records["account"].id)
            matched_id, reason = await self.crm_publisher.match_person(
                job.job_id,
                company_id=company_id,
                linkedin_url=str(data.linkedin_url) if data.linkedin_url else None,
                email=data.email,
                first_name=data.first_name,
                last_name=data.last_name,
                claim_token=token,
            )
            if reason:
                self._complete(job, "needs_review", {"reason": reason})
                return
            identity = (
                normalize_linkedin(str(data.linkedin_url), person=True)
                if data.linkedin_url
                else data.email.casefold()
                if data.email
                else f"{company_id}:{' '.join((data.first_name, data.last_name or '')).strip().casefold()}"
            )
            record_id = matched_id or str(
                deterministic_id(self.crm_publisher.workspace_id, "person", identity)
            )
            if not matched_id:
                await self.crm_publisher.publish_person(
                    job.job_id, record_id, company_id, _person_fields(data), claim_token=token
                )
            refs = [self._ref("person", record_id)]
        else:
            raise ValueError("unsupported research job kind")
        self._complete(
            job,
            outcome,
            {"research_revision": self._research_revision(job), "result_refs": refs},
            refs,
        )

    async def _research(self, job: ResearchJob, stage_key: str, payload: dict[str, Any]) -> Any:
        """Reuse committed research or supervise a child and require its checkpoint."""
        with self.session_factory() as session:
            saved = session.scalar(
                select(ResearchStageResult)
                .where(
                    ResearchStageResult.workflow_id == job.workflow_id,
                    ResearchStageResult.stage_key == stage_key,
                )
                .order_by(ResearchStageResult.revision.desc())
                .limit(1)
            )
            url = session.get_bind().engine.url.render_as_string(hide_password=False)
        if saved is None:
            self._fence(job)
            if job.deadline_at is None:
                raise ValueError("missing workflow deadline")
            deadline = min(
                _aware(job.deadline_at),
                datetime.now(UTC) + timedelta(seconds=self.research_config.stage_timeout_seconds),
            )
            await run_supervised(
                partial(_child_stage, url, job.job_id, job.claim_token or "", stage_key, payload),
                deadline_at=deadline,
                config=ExecutionConfig(
                    self.research_config.initialization_timeout_seconds,
                    self.research_config.cleanup_reserve_seconds,
                ),
            )
            self._fence(job)
            with self.session_factory() as session:
                saved = session.scalar(
                    select(ResearchStageResult)
                    .where(
                        ResearchStageResult.workflow_id == job.workflow_id,
                        ResearchStageResult.stage_key == stage_key,
                    )
                    .order_by(ResearchStageResult.revision.desc())
                    .limit(1)
                )
            if saved is None:
                raise ValueError("research child exited without a committed checkpoint")
        if saved.outcome == "budget_exhausted":
            raise BudgetExhausted("research stage budget exhausted")
        return _stage_model(job.kind, stage_key).model_validate(saved.payload)

    async def _discovery(self, job: ResearchJob, icp: ICPData, limit: int) -> None:
        with self.session_factory() as session:
            saved = session.scalar(
                select(ResearchStageResult)
                .where(
                    ResearchStageResult.workflow_id == job.workflow_id,
                    ResearchStageResult.stage_key == "discovery.research",
                )
                .order_by(ResearchStageResult.revision.desc())
                .limit(1)
            )
        if saved is not None:
            self._complete(job, saved.outcome, saved.payload)
            return
        reset_budget = bind_budget(_stage_budget(self.session_factory, job, self.research_config))
        try:
            service = RetrievalService(SearxngSearchClient(get_search_config(load_settings())))
            candidates: list[dict[str, str]] = []
            seen: set[str] = set()
            for query in (
                f"{icp.sector} {icp.geography} {icp.workflow}",
                f"{icp.buyer_role} {icp.sector} {icp.geography}",
            ):
                self._fence(job)
                response = await service.search(query[:300])
                for candidate in response.results:
                    if candidate.url in seen:
                        continue
                    seen.add(candidate.url)
                    candidates.append(
                        {
                            "name": candidate.title[:200],
                            "website": candidate.url,
                            "reason": candidate.snippet[:500],
                        }
                    )
                    if len(candidates) >= limit:
                        break
                if len(candidates) >= limit:
                    break
            self._complete(
                job,
                "complete" if candidates else "insufficient_evidence",
                {"candidates": candidates},
            )
        finally:
            reset_budget()

    async def _account_hydration(self, job: ResearchJob, payload: dict[str, Any]) -> None:
        """Hydrate only missing fields of the immutable observed Company identity."""
        record = CRMRecord[AccountData].model_validate(payload["observed_records"]["account"])
        await self._hydrate(job, record, payload)

    async def _person_hydration(self, job: ResearchJob, payload: dict[str, Any]) -> None:
        """Hydrate the selected Person without inventing its current Company."""
        if "account" not in payload["crm_inputs"]:
            self._complete(job, "needs_review", {"unknowns": ["person has no current Company"]})
            return
        record = CRMRecord[PersonData].model_validate(payload["observed_records"]["person"])
        await self._hydrate(job, record, payload)

    async def _hydrate(
        self, job: ResearchJob, record: CRMRecord[Any], payload: dict[str, Any]
    ) -> None:
        scope = "account" if job.kind == "account_hydration" else "person"
        object_name = "company" if scope == "account" else "person"
        missing = payload["missing_fields"]
        refs = [self._ref(object_name, str(record.id))]
        if not missing:
            self._complete(job, "complete", {"missing_fields": [], "proposals": {}}, refs)
            return
        result = await self._research(job, f"{job.kind}.research", payload)
        if result.data is None or result.outcome not in {"complete", "partial"}:
            self._complete(job, result.outcome, result.model_dump(mode="json"), refs)
            return
        if scope == "account":
            same_identity = result.data.name.casefold() == record.data.name.casefold()
            if record.data.website and result.data.website:
                same_identity &= normalize_domain(str(record.data.website)) == normalize_domain(
                    str(result.data.website)
                )
            same_identity &= result.qualification.value == "accepted"
        else:
            same_identity = result.data.first_name.casefold() == record.data.first_name.casefold()
            if record.data.last_name:
                same_identity &= (
                    result.data.last_name or ""
                ).casefold() == record.data.last_name.casefold()
        if not same_identity:
            self._complete(job, "needs_review", {"unknowns": ["crm_identity_conflict"]}, refs)
            return
        proposed = (
            _account_fields(result.data) if scope == "account" else _person_fields(result.data)
        )
        mapping = _FIELD_NAMES[scope]
        proposals = {
            mapping[field]: proposed[mapping[field]]
            for field in missing
            if field in mapping and proposed.get(mapping[field]) is not None
        }
        if scope == "person" and "last_name" in missing:
            if result.data.last_name:
                proposals["name"] = {"lastName": result.data.last_name}
            else:
                proposals.pop("name", None)
        proposal_payload = {
            "proposals": proposals,
            "research_revision": self._research_revision(job),
        }
        with self.session_factory.begin() as session:
            persist_stage_result(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token or "",
                workflow_id=job.workflow_id or "",
                stage_key=f"{job.kind}.proposals",
                input_hash=canonical_input_hash(proposal_payload),
                outcome=result.outcome,
                payload=proposal_payload,
                now=datetime.now(UTC),
                unknowns=result.unknowns,
            )
        refreshed = await self.crm_client.read_scope(payload["request"])
        if {key: value.targeting_snapshot() for key, value in refreshed.items()} != payload[
            "crm_inputs"
        ]:
            raise ValueError("crm_input_changed")
        outcomes = await self.crm_publisher.hydrate(
            job.job_id,
            object_name,
            str(record.id),
            record.updated_at.isoformat(),
            proposals,
            claim_token=job.claim_token or "",
        )
        unknowns = [field for field in missing if mapping[field] not in proposals]
        outcome = (
            "partial"
            if unknowns
            or result.outcome == "partial"
            or any(item.get("status") != "confirmed" for item in outcomes)
            else "complete"
        )
        self._complete(
            job, outcome, {**proposal_payload, "writes": outcomes, "unknowns": unknowns}, refs
        )

    def _research_revision(self, job: ResearchJob) -> int | None:
        with self.session_factory() as session:
            return session.scalar(
                select(ResearchStageResult.revision)
                .where(
                    ResearchStageResult.workflow_id == job.workflow_id,
                    ResearchStageResult.stage_key == f"{job.kind}.research",
                )
                .order_by(ResearchStageResult.revision.desc())
                .limit(1)
            )

    def _ref(self, object_name: str, record_id: str) -> dict[str, str]:
        return {
            "object_name": object_name,
            "record_id": record_id,
            "url": self.crm_client.config.public_url,
        }

    def _complete(
        self,
        job: ResearchJob,
        outcome: str,
        payload: dict[str, Any],
        refs: list[dict[str, str]] | None = None,
    ) -> None:
        with self.session_factory.begin() as session:
            current = require_claim(session, job.job_id, job.claim_token or "", datetime.now(UTC))
            if current.workflow_id is None:
                raise ValueError("missing workflow")
            if job.kind != "discovery":
                intents = session.scalars(
                    select(CrmWriteOperation)
                    .where(CrmWriteOperation.job_id == job.job_id)
                    .order_by(CrmWriteOperation.operation_id)
                )
                payload = {
                    **payload,
                    "research_revision": self._research_revision(job),
                    "intent_outcomes": [
                        {"operation_id": item.operation_id, "status": item.status}
                        for item in intents
                    ],
                }
            merged_refs = list(current.result_refs)
            for ref in refs or []:
                if not any(
                    existing["object_name"] == ref["object_name"]
                    and existing["record_id"] == ref["record_id"]
                    for existing in merged_refs
                ):
                    merged_refs.append(ref)
            persist_stage_result(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token or "",
                workflow_id=current.workflow_id,
                stage_key=f"{job.kind}.publication"
                if job.kind != "discovery"
                else "discovery.research",
                input_hash=canonical_input_hash(payload),
                outcome=outcome,
                payload=payload,
                now=datetime.now(UTC),
                unknowns=payload.get("unknowns", []),
            )
            complete_job(
                session,
                job.job_id,
                job.claim_token or "",
                now=datetime.now(UTC),
                result_outcome=outcome,
                result_refs=merged_refs,
            )
            workflow = session.get(ResearchWorkflow, current.workflow_id)
            if workflow:
                workflow.status, workflow.finished_at = "succeeded", datetime.now(UTC)


_FIELD_NAMES = {
    "account": {
        "sector": "malgSector",
        "employees": "malgEmployees",
        "annual_revenue": "annualRevenue",
        "website": "domainName",
        "linkedin_url": "linkedinLink",
    },
    "person": {
        "last_name": "name",
        "job_title": "jobTitle",
        "email": "emails",
        "linkedin_url": "linkedinLink",
    },
}


def _missing_fields(record: CRMRecord[Any]) -> list[str]:
    """Treat zero and partially populated native composites as populated."""
    scope = "account" if isinstance(record.data, AccountData) else "person"
    missing = []
    for field, native in _FIELD_NAMES[scope].items():
        value = getattr(record.data, field)
        if value is not None and value != "":
            continue
        observed = record.observed_composites.get(native, {})
        if field == "last_name" or not any(
            value not in (None, "", [], {}) for value in observed.values()
        ):
            missing.append(field)
    return missing


def _person_fields(data: PersonData) -> dict[str, Any]:
    return {
        "name": {"firstName": data.first_name, "lastName": data.last_name or ""},
        "jobTitle": data.job_title,
        "emails": {"primaryEmail": data.email or ""},
        "linkedinLink": {"primaryLinkUrl": str(data.linkedin_url)} if data.linkedin_url else None,
    }


def _account_fields(data: AccountData) -> dict[str, Any]:
    revenue = None
    if data.annual_revenue is not None:
        micros = data.annual_revenue.amount * 1_000_000
        if micros != micros.to_integral_value() or micros > 9_007_199_254_740_991:
            raise ValueError("currency cannot be represented as exact micros")
        revenue = {
            "amountMicros": str(int(micros)),
            "currencyCode": data.annual_revenue.currency_code,
        }
    return {
        "malgSector": data.sector,
        "malgEmployees": data.employees,
        "annualRevenue": revenue,
        "domainName": {"primaryLinkUrl": str(data.website)} if data.website else None,
        "linkedinLink": {"primaryLinkUrl": str(data.linkedin_url)} if data.linkedin_url else None,
    }


async def worker_loop(worker: ResearchWorker) -> None:
    """Maintain local heartbeat while claiming bounded jobs until cancelled."""

    async def heartbeats() -> None:
        while True:
            await worker.heartbeat()
            await asyncio.sleep(worker.config.poll_interval_seconds)

    heartbeat = asyncio.create_task(heartbeats())
    try:
        while True:
            with suppress(TwentyError):
                await worker.crm_publisher.reconcile_pending()
            if not await worker.run_once():
                await asyncio.sleep(worker.config.poll_interval_seconds)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
