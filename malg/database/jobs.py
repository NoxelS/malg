"""Transactional persistence operations for durable research jobs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from malg.core.models.jobs import (
    AccountResearchJobRequest,
    DiscoveryResearchJobRequest,
    ICPResearchJobRequest,
    QualificationResearchJobRequest,
    ResearchJobKind,
    ResearchJobRequest,
    ResearchJobStatus,
)
from malg.database.models import ResearchJob


def enqueue_job(request: ResearchJobRequest, session: Session) -> ResearchJob:
    """Insert one queued job, copying only identifiers from its request."""
    job = ResearchJob(job_id=str(uuid4()), kind=request.kind.value, status=ResearchJobStatus.QUEUED.value)
    if isinstance(request, (ICPResearchJobRequest, AccountResearchJobRequest, QualificationResearchJobRequest, DiscoveryResearchJobRequest)):
        job.campaign_id = request.campaign_id
    if isinstance(request, (AccountResearchJobRequest, QualificationResearchJobRequest, DiscoveryResearchJobRequest)):
        job.icp_id = request.icp_id
    if isinstance(request, QualificationResearchJobRequest):
        job.failure_detail = json.dumps(request.candidate, sort_keys=True)
    elif isinstance(request, DiscoveryResearchJobRequest):
        job.failure_detail = json.dumps({"limit": request.limit})
    session.add(job)
    session.flush()
    return job


def enqueue_campaign_jobs(amount: int, session: Session) -> list[ResearchJob]:
    """Insert a bounded batch of independently queued campaign jobs."""
    jobs = [
        ResearchJob(
            job_id=str(uuid4()),
            kind=ResearchJobKind.CAMPAIGN.value,
            status=ResearchJobStatus.QUEUED.value,
        )
        for _ in range(amount)
    ]
    session.add_all(jobs)
    session.flush()
    return jobs


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
        if expired_job.attempt_count >= max_attempts:
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
        .where(ResearchJob.status == ResearchJobStatus.QUEUED.value)
        .order_by(ResearchJob.created_at, ResearchJob.job_id)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = ResearchJobStatus.RUNNING.value
    job.attempt_count += 1
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
    account_match_id: str | None = None,
    now: datetime,
) -> ResearchJob:
    """Mark a current fenced claim succeeded."""
    job = require_claim(session, job_id, claim_token, now)
    job.status = ResearchJobStatus.SUCCEEDED.value
    job.campaign_id = campaign_id or job.campaign_id
    job.icp_id = icp_id or job.icp_id
    job.account_match_id = account_match_id
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
    session.flush()
    return job




def cancel_job(session: Session, job_id: str, now: datetime) -> ResearchJob | None:
    """Cancel a queued or running job and clear ownership transactionally."""
    job = session.scalar(
        select(ResearchJob).where(ResearchJob.job_id == job_id).with_for_update()
    )
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
    session.flush()
    return job


def delete_job(session: Session, job_id: str) -> bool:
    """Delete a terminal job record; return ``False`` for absent or active work."""
    job = session.get(ResearchJob, job_id)
    if job is None or job.status not in {
        ResearchJobStatus.SUCCEEDED.value,
        ResearchJobStatus.FAILED.value,
        ResearchJobStatus.CANCELLED.value,
    }:
        return False
    session.delete(job)
    session.flush()
    return True


def require_claim(session: Session, job_id: str, claim_token: str, now: datetime) -> ResearchJob:
    """Lock and validate a live, unexpired claim before any write."""
    job = session.scalar(
        select(ResearchJob).where(ResearchJob.job_id == job_id).with_for_update()
    )
    if (
        job is None
        or job.status != ResearchJobStatus.RUNNING.value
        or job.claim_token != claim_token
        or job.claim_expires_at is None
        or job.claim_expires_at <= now
    ):
        raise ValueError("job claim is no longer current")
    return job
