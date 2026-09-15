"""Host-owned orchestration for one durable research job at a time."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from malg.config import ICPConfig, WorkerConfig, get_icp_config, load_settings
from malg.core.account_probes import AccountProbeService
from malg.core.agent_tracing import (
    AgentTraceRecorder,
    install_trace_log_handler,
    record_active_event,
)
from malg.core.agents.account_research import AccountResearchAgent
from malg.core.agents.account_validation import AccountValidationAgent
from malg.core.agents.campaign_research import CampaignResearchAgent
from malg.core.agents.icp_research import ICPResearchAgent
from malg.core.browser_support import aclose_browser
from malg.core.models.account import AccountIdentity
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPIdentity, ICPResult
from malg.core.persistent_memory_support import close_persistent_memory
from malg.database.artifacts import persist_account_candidate, persist_campaign, persist_icp
from malg.database.jobs import (
    cancel_running_jobs,
    claim_next_job,
    complete_job,
    fail_job,
    renew_claim,
)
from malg.database.models import ICP, Account, Campaign, ResearchJob
from malg.database.workers import record_worker_heartbeat


class ResearchWorker:
    """Claim, execute, and durably finish one research job per invocation."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        config: WorkerConfig,
        icp_config: ICPConfig | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.config = config
        self.icp_config = icp_config
        self.worker_token = str(uuid4())
        install_trace_log_handler()

    async def heartbeat(self) -> None:
        """Persist worker liveness."""
        with self.session_factory.begin() as session:
            record_worker_heartbeat(session, self.worker_token, datetime.now(UTC))

    def reset_interrupted_jobs(self) -> int:
        """Cancel claims left running when this worker process was replaced."""
        with self.session_factory.begin() as session:
            return cancel_running_jobs(session, datetime.now(UTC))

    async def run_once(self) -> bool:
        """Claim and execute one job, returning whether work was claimed."""
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            job = claim_next_job(
                session, self.worker_token, now, self.config.lease_seconds, self.config.max_attempts
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
        trace.event("job_claimed", {"job_id": job.job_id, "kind": job.kind})
        renewal = asyncio.create_task(self._renew_loop(job))
        try:
            await self._dispatch(job, trace)
        except Exception as error:
            trace.event(
                "job_failed", {"error_type": type(error).__name__, "error_message": str(error)}
            )
            trace.finish_failure(error)
            with self.session_factory.begin() as session, suppress(ValueError):
                fail_job(session, job.job_id, self.worker_token, str(error), datetime.now(UTC))
        else:
            trace.event("job_completed", {"job_id": job.job_id})
            trace.finish_success()
        finally:
            reset()
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal
            trace.close()
        return True

    async def _renew_loop(self, job: ResearchJob) -> None:
        """Renew a live claim periodically."""
        interval = max(1, self.config.lease_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            with self.session_factory.begin() as session:
                if not renew_claim(
                    session,
                    job.job_id,
                    self.worker_token,
                    datetime.now(UTC) + timedelta(seconds=self.config.lease_seconds),
                ):
                    return

    async def _dispatch(self, job: ResearchJob, trace: AgentTraceRecorder) -> None:
        """Dispatch strictly by persisted job kind."""
        trace.event("dispatch_started", {"kind": job.kind})
        if job.kind == "campaign":
            candidate = await self._campaign(trace)
            with self.session_factory.begin() as session:
                persist_campaign(candidate, session)
                trace.event(
                    "artifact_persisted", {"artifact": "campaign", "id": candidate.campaign_id}
                )
                complete_job(
                    session,
                    job.job_id,
                    self.worker_token,
                    campaign_id=candidate.campaign_id,
                    now=datetime.now(UTC),
                )
            return
        if job.kind == "icp":
            await self._icp(job, trace)
            return
        if job.kind == "account":
            await self._account(job, trace)
            return
        raise ValueError(f"unsupported research job kind: {job.kind}")

    async def _run_agent(
        self,
        job_id: str,
        parent: AgentTraceRecorder,
        agent: Any,
        method: str,
        call: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run one generated method under a child trace scope."""
        child = AgentTraceRecorder.start_run(self.session_factory, job_id, "agent", agent, method)
        child.attach(agent)
        reset = child.bind()
        try:
            result = await call()
        except Exception as error:
            child.finish_failure(error)
            raise
        else:
            child.finish_success(result)
            return result
        finally:
            reset()
            child.close()

    async def _campaign(self, parent: AgentTraceRecorder) -> CampaignCandidate:
        """Research and close one campaign agent's resources."""
        agent = CampaignResearchAgent(recorder=record_active_event)
        try:
            return cast(
                CampaignCandidate,
                await self._run_agent(
                    parent.job_id or "", parent, agent, "find_campaign", agent.find_campaign
                ),
            )
        finally:
            if hasattr(agent, "browser"):
                await aclose_browser(agent.browser)
            close_persistent_memory(agent)

    async def _icp(self, job: ResearchJob, parent: AgentTraceRecorder) -> None:
        """Research one ICP using stable existing identities."""
        with self.session_factory() as session:
            row = session.get(Campaign, job.campaign_id)
            if row is None:
                raise ValueError("ICP job campaign does not exist.")
            campaign = CampaignCandidate.model_validate(row.payload)
            limit = (self.icp_config or get_icp_config(load_settings())).max_exclusion_cards
            rows = session.scalars(
                select(ICP)
                .where(ICP.campaign_id == job.campaign_id)
                .order_by(ICP.created_at, ICP.id)
            )
            exclusions = [
                ICPIdentity.model_validate(item.payload["identity"]) for item in list(rows)[:limit]
            ]
        agent = ICPResearchAgent(recorder=record_active_event)
        try:
            result = await self._run_agent(
                job.job_id,
                parent,
                agent,
                "research_one",
                lambda: agent.research_one(campaign, exclusions),
            )
        finally:
            if hasattr(agent, "browser"):
                await aclose_browser(agent.browser)
        if result.campaign_id != job.campaign_id:
            raise ValueError("ICP result campaign scope does not match job.")
        with self.session_factory.begin() as session:
            persist_icp(result, session)
            complete_job(
                session,
                job.job_id,
                self.worker_token,
                campaign_id=campaign.campaign_id,
                icp_id=result.icp_id,
                now=datetime.now(UTC),
            )

    async def _account(self, job: ResearchJob, parent: AgentTraceRecorder) -> None:
        """Research, probe, validate, and persist one scoped account."""
        with self.session_factory() as session:
            campaign_row = session.get(Campaign, job.campaign_id)
            if campaign_row is None:
                raise ValueError("Account job campaign does not exist.")
            icp_row = session.scalar(
                select(ICP).where(ICP.campaign_id == job.campaign_id, ICP.icp_id == job.icp_id)
            )
            if icp_row is None:
                raise ValueError("Account job ICP does not exist.")
            campaign = CampaignCandidate.model_validate(campaign_row.payload)
            icp = ICPResult.model_validate(icp_row.payload)
            exclusions = [
                AccountIdentity.model_validate(item.payload["identity"])
                for item in session.scalars(select(Account).order_by(Account.identity_key))
            ]
        researcher = AccountResearchAgent(recorder=record_active_event)
        try:
            candidate = await self._run_agent(
                job.job_id,
                parent,
                researcher,
                "research_one",
                lambda: researcher.research_one(campaign, icp, exclusions),
            )
        finally:
            if hasattr(researcher, "browser"):
                await aclose_browser(researcher.browser)
        if candidate.campaign_id != job.campaign_id or candidate.icp_id != job.icp_id:
            raise ValueError("account result scope does not match job")
        parent.event(
            "account_probe_started", {"account": candidate.identity.model_dump(mode="json")}
        )
        try:
            probes = await AccountProbeService().probe_candidate(candidate)
            parent.event("account_probe_succeeded", {"result": probes})
        except Exception as error:
            parent.event(
                "account_probe_failed",
                {"error_type": type(error).__name__, "error_message": str(error)},
            )
            raise
        validator = AccountValidationAgent(recorder=record_active_event)
        try:
            validation = await self._run_agent(
                job.job_id,
                parent,
                validator,
                "validate_account",
                lambda: validator.validate_account(campaign, icp, candidate, probes),
            )
        finally:
            if hasattr(validator, "browser"):
                await aclose_browser(validator.browser)
        with self.session_factory.begin() as session:
            match = persist_account_candidate(candidate, validation, session)
            complete_job(
                session,
                job.job_id,
                self.worker_token,
                campaign_id=campaign.campaign_id,
                icp_id=icp.icp_id,
                account_match_id=match.account_match_id,
                now=datetime.now(UTC),
            )


async def worker_loop(worker: ResearchWorker) -> None:
    """Run the worker and independent liveness heartbeat until cancelled."""
    worker.reset_interrupted_jobs()

    async def heartbeat_loop() -> None:
        while True:
            await worker.heartbeat()
            await asyncio.sleep(worker.config.poll_interval_seconds)

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        while True:
            if not await worker.run_once():
                await asyncio.sleep(worker.config.poll_interval_seconds)
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
