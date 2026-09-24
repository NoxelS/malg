"""Transactional persistence operations for durable research jobs."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from malg.core.models.jobs import (
    AccountHydrationJobRequest,
    AccountResearchJobRequest,
    DiscoveryResearchJobRequest,
    ICPResearchJobRequest,
    PersonHydrationJobRequest,
    PersonResearchJobRequest,
    ResearchJobKind,
    ResearchJobRequest,
    ResearchJobStatus,
)
from malg.database.models import CrmWriteOperation, ResearchJob, ResearchWorkflow


def enqueue_job(request: ResearchJobRequest, session: Session) -> ResearchJob:
    """Insert one queued job and snapshot its validated request payload."""
    job = ResearchJob(
        job_id=str(uuid4()),
        kind=request.kind.value,
        status=ResearchJobStatus.QUEUED.value,
        request_payload=request.model_dump(mode="json"),
        data_origin="twenty",
    )
    if isinstance(
        request, (ICPResearchJobRequest, AccountResearchJobRequest, DiscoveryResearchJobRequest)
    ):
        job.campaign_id = str(request.campaign_id) if request.campaign_id else None
    if isinstance(request, (AccountResearchJobRequest, DiscoveryResearchJobRequest)):
        job.icp_id = str(request.icp_id)
    if isinstance(request, (AccountHydrationJobRequest,)):
        job.account_id = str(request.account_id)
    if isinstance(request, (PersonResearchJobRequest,)):
        job.account_id = str(request.account_id)
        job.icp_id = str(request.icp_id)
    if isinstance(request, PersonHydrationJobRequest):
        job.person_id = str(request.person_id)
    session.add(job)
    session.flush()
    return job


def enqueue_scoped_jobs(
    kind: ResearchJobKind,
    amount: int,
    session: Session,
    *,
    campaign_id: str | None = None,
    icp_id: str | None = None,
) -> list[ResearchJob]:
    """Insert a bounded batch of queued jobs with the supplied scope."""
    if not 1 <= amount <= 100:
        raise ValueError("batch amount must be between 1 and 100")
    payload = {"kind": kind.value}
    if campaign_id is not None:
        payload["campaign_id"] = campaign_id
    if icp_id is not None:
        payload["icp_id"] = icp_id
    request: ResearchJobRequest = TypeAdapter(ResearchJobRequest).validate_python(payload)
    return [enqueue_job(request, session) for _ in range(amount)]


def enqueue_campaign_jobs(amount: int, session: Session) -> list[ResearchJob]:
    """Insert a bounded batch of independently queued campaign jobs."""
    return enqueue_scoped_jobs(ResearchJobKind.CAMPAIGN, amount, session)


def claim_next_job(
    session: Session, worker_token: str, now: datetime, lease_seconds: int, max_attempts: int
) -> ResearchJob | None:
    """Reconcile expired claims and atomically claim the oldest eligible job."""
    expired = session.scalars(
        select(ResearchJob)
        .where(
            ResearchJob.status == ResearchJobStatus.RUNNING.value,
            ResearchJob.claim_expires_at <= now,
        )
        .with_for_update(skip_locked=True)
    )
    for expired_job in expired:
        if expired_job.attempt_window_count >= max_attempts:
            expired_job.status = ResearchJobStatus.FAILED.value
            expired_job.failure_detail = "worker claim expired"
            expired_job.finished_at = now
        else:
            expired_job.status = ResearchJobStatus.QUEUED.value
        expired_job.claim_token = None
        expired_job.owner_worker_token = None
        expired_job.claim_expires_at = None
        expired_job.claimed_at = None
    session.flush()
    job: ResearchJob | None = session.scalar(
        select(ResearchJob)
        .where(
            ResearchJob.status == ResearchJobStatus.QUEUED.value,
            ResearchJob.data_origin == "twenty",
        )
        .order_by(ResearchJob.created_at, ResearchJob.job_id)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = ResearchJobStatus.RUNNING.value
    job.attempt_count += 1
    job.attempt_window_count += 1
    job.owner_worker_token = worker_token
    job.claim_token = str(uuid4())
    job.claim_expires_at = now + timedelta(seconds=lease_seconds)
    job.claimed_at = now
    job.started_at = job.started_at or now
    job.stage_started_at = now
    session.flush()
    return job


def renew_claim(
    session: Session, job_id: str, claim_token: str, now: datetime, expires_at: datetime
) -> bool:
    """Extend a claim only when its token is current and not already expired."""
    result = session.execute(
        update(ResearchJob)
        .where(
            ResearchJob.job_id == job_id,
            ResearchJob.status == ResearchJobStatus.RUNNING.value,
            ResearchJob.claim_token == claim_token,
            ResearchJob.claim_expires_at > now,
        )
        .values(claim_expires_at=expires_at)
    )
    return bool(getattr(result, "rowcount", 0) == 1)


def complete_job(
    session: Session,
    job_id: str,
    claim_token: str,
    *,
    campaign_id: str | None = None,
    icp_id: str | None = None,
    result_outcome: str = "complete",
    result_refs: list[dict[str, str]] | None = None,
    now: datetime,
) -> ResearchJob:
    """Mark a current fenced claim succeeded."""
    job = require_claim(session, job_id, claim_token, now)
    job.status = ResearchJobStatus.SUCCEEDED.value
    job.campaign_id = campaign_id or job.campaign_id
    job.icp_id = icp_id or job.icp_id
    job.result_outcome = result_outcome
    if result_refs is not None:
        job.result_refs = result_refs
    job.finished_at = now
    job.claim_token = None
    job.owner_worker_token = None
    job.claim_expires_at = None
    job.claimed_at = None
    session.flush()
    return job


def fail_job(
    session: Session, job_id: str, claim_token: str, detail: str, now: datetime
) -> ResearchJob:
    """Mark a current fenced claim failed."""
    job = require_claim(session, job_id, claim_token, now)
    job.status = ResearchJobStatus.FAILED.value
    job.failure_detail = detail[:2000] or "research job failed"
    job.finished_at = now
    job.claim_token = None
    job.owner_worker_token = None
    job.claim_expires_at = None
    job.claimed_at = None
    if job.workflow_id:
        workflow = session.get(ResearchWorkflow, job.workflow_id)
        if workflow is not None:
            workflow.status, workflow.finished_at = "failed", now
    session.flush()
    return job


def cancel_job(session: Session, job_id: str, now: datetime) -> ResearchJob | None:
    """Cancel a queued or running job and clear ownership transactionally."""
    job = session.scalar(select(ResearchJob).where(ResearchJob.job_id == job_id).with_for_update())
    if job is None or job.status not in {
        ResearchJobStatus.QUEUED.value,
        ResearchJobStatus.RUNNING.value,
    }:
        return None
    job.status = ResearchJobStatus.CANCELLED.value
    job.finished_at = now
    job.claim_token = None
    job.owner_worker_token = None
    job.claim_expires_at = None
    job.claimed_at = None
    if job.workflow_id:
        workflow = session.get(ResearchWorkflow, job.workflow_id)
        if workflow is not None:
            workflow.status, workflow.finished_at = "cancelled", now
    session.flush()
    return job


def delete_job(session: Session, job_id: str) -> bool:
    """Delete terminal work only after every remote write is resolved."""
    job = session.get(ResearchJob, job_id)
    if job is None or job.status not in {
        ResearchJobStatus.SUCCEEDED.value,
        ResearchJobStatus.FAILED.value,
        ResearchJobStatus.CANCELLED.value,
    }:
        return False
    unresolved_write = session.scalar(
        select(CrmWriteOperation.operation_id).where(
            CrmWriteOperation.job_id == job_id,
            CrmWriteOperation.status.not_in(("confirmed", "cancelled")),
        )
    )
    if unresolved_write is not None:
        return False
    session.delete(job)
    session.flush()
    return True


def retry_failed_job(session: Session, job_id: str, now: datetime) -> ResearchJob | None:
    """Requeue one failed job while preserving its identity and request snapshot."""
    job = session.scalar(select(ResearchJob).where(ResearchJob.job_id == job_id).with_for_update())
    if job is None or job.status != ResearchJobStatus.FAILED.value or job.data_origin != "twenty":
        return None
    job.attempt_window_count = 0
    job.status = ResearchJobStatus.QUEUED.value
    job.failure_detail = None
    job.failure_code = None
    job.started_at = None
    job.stage_started_at = None
    job.finished_at = None
    job.deadline_at = None
    job.claim_token = None
    job.claim_expires_at = None
    job.result_outcome = None
    if job.workflow_id:
        workflow = session.get(ResearchWorkflow, job.workflow_id)
        if workflow is not None:
            workflow.started_at = None
            workflow.deadline_at = None
            workflow.finished_at = None
            workflow.status = "queued"
            workflow.llm_attempts = 0
            workflow.search_attempts = 0
            workflow.fetch_attempts = 0
            workflow.followup_used = False
    job.updated_at = now
    session.flush()
    return job


def require_claim(session: Session, job_id: str, claim_token: str, now: datetime) -> ResearchJob:
    """Lock and validate a live, unexpired claim before any write."""
    job = session.scalar(select(ResearchJob).where(ResearchJob.job_id == job_id).with_for_update())
    if (
        job is None
        or job.status != ResearchJobStatus.RUNNING.value
        or job.claim_token != claim_token
        or job.claim_expires_at is None
        or (
            job.claim_expires_at.replace(tzinfo=now.tzinfo)
            if job.claim_expires_at.tzinfo is None
            else job.claim_expires_at
        )
        <= now
    ):
        raise ValueError("job claim is no longer current")
    return job
