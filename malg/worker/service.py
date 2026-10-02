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
    load_settings,
)
from malg.core.agent_tracing import (
    AgentTraceRecorder,
    install_trace_log_handler,
    record_active_event,
)
from malg.core.budget import BudgetExhausted, ResearchBudget, bind_budget
from malg.core.claims import validate_claim_proposal
from malg.core.models.account import (
    AccountData,
    AccountIdentity,
    AccountResearchResult,
    AccountValidationAssessment,
)
from malg.core.models.campaign import CampaignData
from malg.core.models.icp import ICPData
from malg.core.models.opportunity import OpportunityData, OpportunityResearchResult
from malg.core.models.person import PersonData
from malg.core.models.research import ResearchOutcome, ResearchResult
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
    if kind == "opportunity":
        return OpportunityResearchResult
    if kind == "account":
        return AccountResearchResult
    if kind == "person":
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
    units = _workflow_units(job)
    limits = {
        "llm": config.max_llm_attempts_per_workflow * units,
        "search": config.max_search_requests * units,
        "fetch": config.max_fetch_requests * units,
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


def _workflow_units(job: ResearchJob) -> int:
    """Scale a bounded workflow by the requested number of autonomous result bundles."""
    request = job.request_payload or {}
    if job.kind == "icp":
        return max(1, int(request.get("icp_count", 1)))
    if job.kind == "account":
        companies = max(1, int(request.get("company_count", 1)))
        people = max(1, int(request.get("people_per_company", 1)))
        opportunities = max(1, int(request.get("opportunities_per_company", 1)))
        return companies * (1 + people + opportunities)
    return 1


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
    from malg.core.agents.opportunity import OpportunityAgent
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
            workflow_id = workflow.workflow_id
            input_hash = canonical_input_hash(payload)
            kind = str(payload.get("stage_kind", job.kind))
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
        elif kind == "opportunity":
            agent, method = OpportunityAgent(), "pitch_one"
        elif kind == "campaign":
            agent, method = CampaignResearchAgent(recorder=record_active_event), "find_campaign"
        elif kind == "icp":
            agent, method = ICPResearchAgent(recorder=record_active_event), "research_one"
        elif kind == "account":
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
        elif kind == "opportunity":
            result = await agent.pitch_one(
                campaign,
                icp,
                AccountData.model_validate(scopes["account"]["data"]),
                PersonData.model_validate(scopes["person"]["data"]),
                exclusions=[
                    OpportunityData.model_validate(item) for item in payload.get("exclusions", [])
                ],
            )
        elif kind == "campaign":
            result = await agent.find_campaign()
        elif kind == "icp":
            result = await agent.research_one(
                campaign,
                [ICPData.model_validate(item) for item in payload.get("exclusions", [])],
            )
        elif kind == "account":
            result = await agent.research_one(
                campaign,
                icp,
                [AccountIdentity.model_validate(item) for item in payload.get("exclusions", [])],
            )
        else:
            company = scopes["account"]
            result = await agent.research_one(
                UUID(company["id"]),
                company["data"]["name"],
                icp,
                exclusions=[
                    PersonData.model_validate(item) for item in payload.get("exclusions", [])
                ],
            )
        result = _stage_model(kind, stage_key).model_validate(result)
        fetches = [] if kind == "opportunity" else agent.retrieval.observations
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
    """Run three autonomous job kinds with durable research and fenced publication."""

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
        with self.session_factory.begin() as session:
            current = require_claim(session, job.job_id, job.claim_token or "", datetime.now(UTC))
            workflow = create_workflow_for_job(
                session,
                current,
                payload,
                datetime.now(UTC)
                + timedelta(
                    seconds=self.research_config.workflow_timeout_seconds * _workflow_units(current)
                ),
            )
            if workflow.input_payload.get("crm_inputs") != inputs:
                raise ValueError("crm_input_changed")
            job.workflow_id, job.deadline_at = workflow.workflow_id, workflow.deadline_at
            job.input_hash = workflow.input_hash
            payload = dict(workflow.input_payload)
        token = job.claim_token or ""
        if job.kind == "campaign":
            result = await self._research(job, "campaign.research", payload, stage_kind="campaign")
            if result.data is None or result.outcome not in {"complete", "partial"}:
                self._complete(job, result.outcome, result.model_dump(mode="json"))
                return
            record_id = await self.crm_publisher.publish_campaign(
                job.job_id, result.data, claim_token=token
            )
            ref = self._ref("malgCampaign", record_id)
            self._complete(job, result.outcome, {"result_refs": [ref]}, [ref])
            return
        if job.kind == "icp":
            await self._icp_pipeline(job, payload, records)
            return
        if job.kind == "account":
            await self._account_pipeline(job, payload, records)
            return
        raise ValueError("unsupported research job kind")

    async def _research(
        self, job: ResearchJob, stage_key: str, payload: dict[str, Any], *, stage_kind: str
    ) -> Any:
        """Reuse committed research or supervise a child and require its checkpoint."""
        payload = {**payload, "stage_kind": stage_kind}
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
        return _stage_model(stage_kind, stage_key).model_validate(saved.payload)

    async def _icp_pipeline(
        self, job: ResearchJob, payload: dict[str, Any], records: dict[str, CRMRecord[Any]]
    ) -> None:
        """Research and publish the requested number of distinct ICPs."""
        target = int(payload["request"].get("icp_count", 1))
        exclusions: list[ICPData] = []
        refs: list[dict[str, str]] = []
        attempts = 0
        budget_limited = False
        for attempt in range(target * 3):
            if len(refs) >= target:
                break
            attempts += 1
            stage_payload = {
                **payload,
                "exclusions": [item.model_dump(mode="json") for item in exclusions],
            }
            try:
                result = await self._research(
                    job, f"icp.candidate.{attempt}.research", stage_payload, stage_kind="icp"
                )
            except (BudgetExhausted, TimeoutError):
                budget_limited = True
                break
            if result.data is None or result.outcome not in {"complete", "partial"}:
                continue
            if result.data in exclusions:
                continue
            exclusions.append(result.data)
            await self._verify_scope(payload)
            identifier = await self.crm_publisher.publish_icp(
                job.job_id,
                result.data,
                campaign_id=str(records["campaign"].id),
                claim_token=job.claim_token or "",
            )
            refs.append(self._ref("malgIcp", identifier))
            self._save_progress(
                job,
                "complete" if len(refs) == target else "partial",
                {
                    "target_counts": {"icps": target},
                    "achieved_counts": {"icps": len(refs)},
                    "attempts": attempts,
                    "result_refs": refs,
                },
            )
        achieved = len(refs)
        outcome = (
            "complete"
            if achieved == target
            else "partial"
            if achieved
            else "budget_exhausted"
            if budget_limited
            else "insufficient_evidence"
        )
        self._complete(
            job,
            outcome,
            {
                "target_counts": {"icps": target},
                "achieved_counts": {"icps": achieved},
                "attempts": attempts,
                "result_refs": refs,
            },
            refs,
        )

    async def _account_pipeline(
        self, job: ResearchJob, payload: dict[str, Any], records: dict[str, CRMRecord[Any]]
    ) -> None:
        """Publish complete Company bundles without exposing internal stages as jobs."""
        request = payload["request"]
        company_target = int(request.get("company_count", 1))
        people_target = int(request.get("people_per_company", 1))
        opportunity_target = int(request.get("opportunities_per_company", 1))
        account_exclusions: list[AccountIdentity] = []
        bundles: list[dict[str, Any]] = []
        refs: list[dict[str, str]] = []
        budget_limited = False
        attempts = 0

        for candidate_index in range(company_target * 3):
            if len(bundles) >= company_target:
                break
            attempts += 1
            account_payload = {
                **payload,
                "exclusions": [item.model_dump(mode="json") for item in account_exclusions],
            }
            try:
                account_result = await self._research(
                    job,
                    f"account.candidate.{candidate_index}.research",
                    account_payload,
                    stage_kind="account",
                )
            except (BudgetExhausted, TimeoutError):
                budget_limited = True
                break
            if account_result.identity is not None:
                account_exclusions.append(account_result.identity)
            if (
                account_result.data is None
                or account_result.identity is None
                or account_result.outcome not in {"complete", "partial"}
                or account_result.qualification.value != "accepted"
            ):
                continue
            try:
                validation = await self._research(
                    job,
                    f"account.candidate.{candidate_index}.validation",
                    {**account_payload, "research": account_result.model_dump(mode="json")},
                    stage_kind="account",
                )
            except (BudgetExhausted, TimeoutError):
                budget_limited = True
                break
            if not isinstance(validation, AccountValidationAssessment):
                raise ValueError("account_validation_missing")
            if validation.outcome.value != "accepted":
                continue
            company_id, reason = await self.crm_publisher.match_company(
                job.job_id, account_result, claim_token=job.claim_token or ""
            )
            if reason:
                continue
            domain = _account_domain(account_result)
            if domain is None:
                continue
            company_id = company_id or str(
                deterministic_id(self.crm_publisher.workspace_id, "company", domain)
            )
            try:
                people = await self._research_people(
                    job,
                    payload,
                    account_result.data,
                    company_id,
                    candidate_index,
                    people_target,
                )
            except (BudgetExhausted, TimeoutError):
                budget_limited = True
                break
            if len(people) < people_target:
                continue
            try:
                opportunities = await self._research_opportunities(
                    job,
                    payload,
                    account_result.data,
                    people,
                    company_id,
                    candidate_index,
                    opportunity_target,
                )
            except (BudgetExhausted, TimeoutError):
                budget_limited = True
                break
            if len(opportunities) < opportunity_target:
                continue

            await self._verify_scope(payload)
            (
                published_company,
                membership_id,
                publish_reason,
            ) = await self.crm_publisher.publish_account(
                job.job_id,
                account_result,
                validation,
                icp_id=str(records["icp"].id),
                claim_token=job.claim_token or "",
            )
            if publish_reason or published_company is None or membership_id is None:
                continue
            await self._enrich_record(job, "account", published_company, account_result.data)
            bundle_refs = [
                self._ref("company", published_company),
                self._ref("malgMembership", membership_id),
            ]
            published_people: list[str] = []
            for person_data, person_id, matched in people:
                if not matched:
                    await self.crm_publisher.publish_person(
                        job.job_id,
                        person_id,
                        published_company,
                        _person_fields(person_data),
                        claim_token=job.claim_token or "",
                    )
                await self._enrich_record(job, "person", person_id, person_data)
                published_people.append(person_id)
                bundle_refs.append(self._ref("person", person_id))
            for opportunity_index, (opportunity_data, unknowns, person_slot) in enumerate(
                opportunities
            ):
                opportunity_id, note_id, target_id = await self.crm_publisher.publish_opportunity(
                    job.job_id,
                    opportunity_data,
                    campaign_id=str(records["campaign"].id),
                    icp_id=str(records["icp"].id),
                    company_id=published_company,
                    person_id=published_people[person_slot],
                    unknowns=unknowns,
                    stable_key=f"{published_company}:{opportunity_index}",
                    claim_token=job.claim_token or "",
                )
                bundle_refs.extend(
                    self._ref(name, identifier)
                    for name, identifier in (
                        ("opportunity", opportunity_id),
                        ("note", note_id),
                        ("noteTarget", target_id),
                    )
                )
            refs.extend(bundle_refs)
            bundles.append(
                {
                    "company_id": published_company,
                    "person_ids": published_people,
                    "opportunity_ids": [
                        item["record_id"]
                        for item in bundle_refs
                        if item["object_name"] == "opportunity"
                    ],
                }
            )
            self._save_progress(
                job,
                "complete" if len(bundles) == company_target else "partial",
                {
                    "target_counts": {
                        "companies": company_target,
                        "people": company_target * people_target,
                        "opportunities": company_target * opportunity_target,
                    },
                    "achieved_counts": {
                        "companies": len(bundles),
                        "people": sum(len(bundle["person_ids"]) for bundle in bundles),
                        "opportunities": sum(len(bundle["opportunity_ids"]) for bundle in bundles),
                    },
                    "candidate_attempts": attempts,
                    "bundles": bundles,
                    "result_refs": refs,
                },
            )

        achieved_companies = len(bundles)
        achieved_people = sum(len(bundle["person_ids"]) for bundle in bundles)
        achieved_opportunities = sum(len(bundle["opportunity_ids"]) for bundle in bundles)
        outcome = (
            "complete"
            if achieved_companies == company_target
            else "partial"
            if achieved_companies
            else "budget_exhausted"
            if budget_limited
            else "insufficient_evidence"
        )
        self._complete(
            job,
            outcome,
            {
                "target_counts": {
                    "companies": company_target,
                    "people": company_target * people_target,
                    "opportunities": company_target * opportunity_target,
                },
                "achieved_counts": {
                    "companies": achieved_companies,
                    "people": achieved_people,
                    "opportunities": achieved_opportunities,
                },
                "candidate_attempts": attempts,
                "bundles": bundles,
                "result_refs": refs,
            },
            refs,
        )

    async def _research_people(
        self,
        job: ResearchJob,
        payload: dict[str, Any],
        account: AccountData,
        company_id: str,
        candidate_index: int,
        target: int,
    ) -> list[tuple[PersonData, str, bool]]:
        """Research distinct people for one planned Company without publishing them yet."""
        exclusions: list[PersonData] = []
        people: list[tuple[PersonData, str, bool]] = []
        for person_attempt in range(target * 3):
            if len(people) >= target:
                break
            person_payload = {
                **payload,
                "crm_inputs": {
                    **payload["crm_inputs"],
                    "account": {"id": company_id, "data": account.model_dump(mode="json")},
                },
                "exclusions": [item.model_dump(mode="json") for item in exclusions],
            }
            result = await self._research(
                job,
                f"account.candidate.{candidate_index}.person.{person_attempt}.research",
                person_payload,
                stage_kind="person",
            )
            if result.data is None or result.outcome not in {"complete", "partial"}:
                continue
            exclusions.append(result.data)
            matched_id, reason = await self.crm_publisher.match_person(
                job.job_id,
                company_id=company_id,
                linkedin_url=str(result.data.linkedin_url) if result.data.linkedin_url else None,
                email=result.data.email,
                first_name=result.data.first_name,
                last_name=result.data.last_name,
                claim_token=job.claim_token or "",
            )
            if reason:
                continue
            person_id = matched_id or str(
                deterministic_id(
                    self.crm_publisher.workspace_id,
                    "person",
                    _person_identity(company_id, result.data),
                )
            )
            if any(existing_id == person_id for _, existing_id, _ in people):
                continue
            people.append((result.data, person_id, matched_id is not None))
        return people

    async def _research_opportunities(
        self,
        job: ResearchJob,
        payload: dict[str, Any],
        account: AccountData,
        people: list[tuple[PersonData, str, bool]],
        company_id: str,
        candidate_index: int,
        target: int,
    ) -> list[tuple[OpportunityData, list[str], int]]:
        """Research distinct opportunities and retain the associated person slot."""
        exclusions: list[OpportunityData] = []
        opportunities: list[tuple[OpportunityData, list[str], int]] = []
        for opportunity_attempt in range(target * 2):
            if len(opportunities) >= target:
                break
            person_slot = len(opportunities) % len(people)
            person = people[person_slot][0]
            opportunity_payload = {
                **payload,
                "crm_inputs": {
                    **payload["crm_inputs"],
                    "account": {"id": company_id, "data": account.model_dump(mode="json")},
                    "person": {
                        "id": people[person_slot][1],
                        "data": person.model_dump(mode="json"),
                    },
                },
                "exclusions": [item.model_dump(mode="json") for item in exclusions],
            }
            result = await self._research(
                job,
                f"account.candidate.{candidate_index}.opportunity.{opportunity_attempt}.research",
                opportunity_payload,
                stage_kind="opportunity",
            )
            if result.data is None or result.outcome not in {"complete", "partial"}:
                continue
            if any(item.name.casefold() == result.data.name.casefold() for item in exclusions):
                continue
            exclusions.append(result.data)
            opportunities.append((result.data, result.unknowns, person_slot))
        return opportunities

    async def _enrich_record(
        self, job: ResearchJob, scope: str, record_id: str, researched: Any
    ) -> None:
        """Fill verified empty CRM fields as part of normal publication."""
        record = (
            await self.crm_client.get_account(UUID(record_id))
            if scope == "account"
            else await self.crm_client.get_person(UUID(record_id))
        )
        missing = _missing_fields(record)
        proposed = _account_fields(researched) if scope == "account" else _person_fields(researched)
        mapping = _FIELD_NAMES[scope]
        proposals = {
            mapping[field]: proposed[mapping[field]]
            for field in missing
            if proposed.get(mapping[field]) is not None
        }
        if scope == "person" and "last_name" in missing:
            proposals["name"] = {"lastName": researched.last_name} if researched.last_name else None
            proposals = {key: value for key, value in proposals.items() if value is not None}
        if proposals:
            await self.crm_publisher.fill_missing(
                job.job_id,
                "company" if scope == "account" else "person",
                record_id,
                record.updated_at.isoformat(),
                proposals,
                claim_token=job.claim_token or "",
            )

    async def _verify_scope(self, payload: dict[str, Any]) -> None:
        """Prevent publication when the Campaign or ICP changed during research."""
        refreshed = await self.crm_client.read_scope(payload["request"])
        if {key: value.targeting_snapshot() for key, value in refreshed.items()} != payload[
            "crm_inputs"
        ]:
            raise ValueError("crm_input_changed")

    def _ref(self, object_name: str, record_id: str) -> dict[str, str]:
        return {
            "object_name": object_name,
            "record_id": record_id,
            "url": self.crm_client.config.public_url,
        }

    def _save_progress(self, job: ResearchJob, outcome: str, payload: dict[str, Any]) -> None:
        """Append claim-fenced target progress after each fully published result unit."""
        with self.session_factory.begin() as session:
            current = require_claim(session, job.job_id, job.claim_token or "", datetime.now(UTC))
            if current.workflow_id is None:
                raise ValueError("missing workflow")
            persist_stage_result(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token or "",
                workflow_id=current.workflow_id,
                stage_key=f"{job.kind}.progress",
                input_hash=canonical_input_hash(payload),
                outcome=outcome,
                payload=payload,
                now=datetime.now(UTC),
            )

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
            intents = session.scalars(
                select(CrmWriteOperation)
                .where(CrmWriteOperation.job_id == job.job_id)
                .order_by(CrmWriteOperation.operation_id)
            )
            payload = {
                **payload,
                "intent_outcomes": [
                    {"operation_id": item.operation_id, "status": item.status} for item in intents
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
                stage_key=f"{job.kind}.publication",
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


def _account_domain(result: AccountResearchResult) -> str | None:
    """Return the observed domain that defines a stable Company identity."""
    if result.identity and result.identity.official_website:
        return normalize_domain(str(result.identity.official_website))
    if result.data and result.data.website:
        return normalize_domain(str(result.data.website))
    return None


def _person_identity(company_id: str, data: PersonData) -> str:
    """Return a stable Person identity scoped to the verified Company."""
    if data.linkedin_url:
        return normalize_linkedin(str(data.linkedin_url), person=True)
    if data.email:
        return data.email.casefold()
    name = " ".join((data.first_name, data.last_name or "")).strip().casefold()
    return f"{company_id}:{name}"


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
