"""Versioned API routes for enqueueing and managing research jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from malg.api.routers.artifacts import SessionDependency, _campaign_or_404, _icp_or_404
from malg.core.models.jobs import (
    AccountResearchJobRequest,
    CampaignResearchJobBatchRequest,
    ICPResearchJobRequest,
    ResearchJobRecord,
    ResearchJobRequest,
    ResearchJobStatus,
)
from malg.database.jobs import cancel_job, enqueue_campaign_jobs, enqueue_job
from malg.database.models import ResearchJob

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _record(job: ResearchJob) -> ResearchJobRecord:
    """Convert one ORM row to the public job contract."""
    return ResearchJobRecord.model_validate(
        {
            "job_id": job.job_id,
            "kind": job.kind,
            "status": job.status,
            "campaign_id": job.campaign_id,
            "icp_id": job.icp_id,
            "account_match_id": job.account_match_id,
            "attempt_count": job.attempt_count,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "failure_detail": job.failure_detail,
        }
    )


def _job_or_404(session: Session, job_id: str) -> ResearchJob:
    """Return one job or the stable not-found response."""
    job = session.get(ResearchJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("", response_model=ResearchJobRecord, status_code=status.HTTP_202_ACCEPTED)
def create_job(payload: ResearchJobRequest, session: SessionDependency) -> ResearchJobRecord:
    """Validate parent scope and enqueue exactly one research unit."""
    if isinstance(payload, ICPResearchJobRequest):
        _campaign_or_404(session, payload.campaign_id)
    elif isinstance(payload, AccountResearchJobRequest):
        _campaign_or_404(session, payload.campaign_id)
        _icp_or_404(session, payload.campaign_id, payload.icp_id)
    job = enqueue_job(payload, session)
    session.commit()
    return _record(job)


@router.post(
    "/campaigns",
    response_model=list[ResearchJobRecord],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_campaign_jobs(
    payload: CampaignResearchJobBatchRequest, session: SessionDependency
) -> list[ResearchJobRecord]:
    """Enqueue a bounded batch of independent campaign research jobs."""
    jobs = enqueue_campaign_jobs(payload.amount, session)
    session.commit()
    return [_record(job) for job in jobs]


@router.get("", response_model=list[ResearchJobRecord])
def list_jobs(
    session: SessionDependency,
    job_status: Annotated[ResearchJobStatus | None, Query(alias="status")] = None,
) -> list[ResearchJobRecord]:
    """List jobs newest first, optionally restricted to one lifecycle status."""
    query = select(ResearchJob).order_by(ResearchJob.created_at.desc(), ResearchJob.job_id.desc())
    if job_status is not None:
        query = query.where(ResearchJob.status == job_status.value)
    return [_record(job) for job in session.scalars(query)]


@router.get("/{job_id}", response_model=ResearchJobRecord)
def get_job(job_id: str, session: SessionDependency) -> ResearchJobRecord:
    """Return one durable job record."""
    return _record(_job_or_404(session, job_id))


@router.post("/{job_id}/cancel", response_model=ResearchJobRecord)
def cancel_research_job(job_id: str, session: SessionDependency) -> ResearchJobRecord:
    """Cancel queued work without interrupting a running agent."""
    _job_or_404(session, job_id)
    job = cancel_job(session, job_id, datetime.now(UTC))
    if job is None:
        raise HTTPException(status_code=409, detail="job cannot be cancelled")
    session.commit()
    return _record(job)
