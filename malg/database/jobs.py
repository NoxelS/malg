"""Transactional persistence operations for durable research jobs."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from malg.core.models.jobs import (
    AccountResearchJobRequest,
    ICPResearchJobRequest,
    ResearchJobRequest,
    ResearchJobStatus,
)
from malg.database.models import ResearchJob


def enqueue_job(request: ResearchJobRequest, session: Session) -> ResearchJob:
    """Insert one queued job, copying only identifiers from its request."""
    job = ResearchJob(
        job_id=str(uuid4()), kind=request.kind.value, status=ResearchJobStatus.QUEUED.value
    )
    if isinstance(request, (ICPResearchJobRequest, AccountResearchJobRequest)):
        job.campaign_id = request.campaign_id
    if isinstance(request, AccountResearchJobRequest):
        job.icp_id = request.icp_id
    session.add(job)
    session.flush()
    return job


def claim_next_job(
    session: Session, worker_token: str, now: datetime, lease_seconds: int, max_attempts: int
) -> ResearchJob | None:
    """Reconcile expired claims and atomically claim the oldest eligible job."""
    expired = session.scalars(
        select(ResearchJob).where(
            ResearchJob.status == ResearchJobStatus.RUNNING.value,
            ResearchJob.claim_expires_at <= now,
        )
    )
    for expired_job in expired:
        if expired_job.attempt_count >= max_attempts:
            expired_job.status = ResearchJobStatus.FAILED.value
            expired_job.failure_detail = "worker claim expired"
            expired_job.finished_at = now
        else:
            expired_job.status = ResearchJobStatus.QUEUED.value
        expired_job.claim_token = None
        expired_job.claim_expires_at = None
    session.flush()
    job: ResearchJob | None = session.scalar(
        select(ResearchJob)
        .where(ResearchJob.status == ResearchJobStatus.QUEUED.value)
        .order_by(ResearchJob.created_at, ResearchJob.job_id)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = ResearchJobStatus.RUNNING.value
    job.attempt_count += 1
    job.claim_token = worker_token
    job.claim_expires_at = now + timedelta(seconds=lease_seconds)
    job.started_at = job.started_at or now
    session.flush()
    return job


def renew_claim(session: Session, job_id: str, claim_token: str, expires_at: datetime) -> bool:
    """Extend a live claim only while its token remains current."""
    result = session.execute(
        update(ResearchJob)
        .where(
            ResearchJob.job_id == job_id,
            ResearchJob.status == ResearchJobStatus.RUNNING.value,
            ResearchJob.claim_token == claim_token,
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
    account_match_id: str | None = None,
    now: datetime,
) -> ResearchJob:
    """Mark the current claim succeeded and attach persisted artifact identifiers."""
    job = _claimed(session, job_id, claim_token)
    job.status = ResearchJobStatus.SUCCEEDED.value
    job.campaign_id = campaign_id or job.campaign_id
    job.icp_id = icp_id or job.icp_id
    job.account_match_id = account_match_id
    job.finished_at = now
    job.claim_token = None
    job.claim_expires_at = None
    session.flush()
    return job


def fail_job(
    session: Session, job_id: str, claim_token: str, detail: str, now: datetime
) -> ResearchJob:
    """Mark the current claim failed with concise host-owned error detail."""
    job = _claimed(session, job_id, claim_token)
    job.status = ResearchJobStatus.FAILED.value
    job.failure_detail = detail[:2000] or "research job failed"
    job.finished_at = now
    job.claim_token = None
    job.claim_expires_at = None
    session.flush()
    return job


def cancel_job(session: Session, job_id: str, now: datetime) -> ResearchJob | None:
    """Cancel a queued job; return ``None`` when it is not cancellable."""
    job = session.get(ResearchJob, job_id)
    if job is None or job.status != ResearchJobStatus.QUEUED.value:
        return None
    job.status = ResearchJobStatus.CANCELLED.value
    job.finished_at = now
    session.flush()
    return job


def _claimed(session: Session, job_id: str, claim_token: str) -> ResearchJob:
    job = session.get(ResearchJob, job_id)
    if (
        job is None
        or job.status != ResearchJobStatus.RUNNING.value
        or job.claim_token != claim_token
    ):
        raise ValueError("job claim is no longer current")
    return job
