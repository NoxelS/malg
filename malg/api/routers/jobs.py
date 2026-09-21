"""Versioned API routes for enqueueing and managing research jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from malg.api.routers.artifacts import SessionDependency, _campaign_or_404, _icp_or_404
from malg.core.models.jobs import (
    AccountResearchJobBatchRequest, AccountResearchJobRequest,
    CampaignResearchJobBatchRequest, DiscoveryResearchJobRequest,
    ICPResearchJobBatchRequest, ICPResearchJobRequest,
    QualificationResearchJobRequest, ResearchJobRecord,
    ResearchJobRequest, ResearchJobStatus,
    ResearchJobKind,
)
from malg.database.jobs import (
    cancel_job, delete_job, enqueue_campaign_jobs, enqueue_job, enqueue_scoped_jobs,
)
from malg.database.models import ResearchJob, ResearchStageResult, ResearchWorkflow

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _record(job: ResearchJob) -> ResearchJobRecord:
    """Convert one ORM row to the public job contract."""
    return ResearchJobRecord.model_validate(
        {
            "job_id": job.job_id, "kind": job.kind, "status": job.status,
            "campaign_id": job.campaign_id, "icp_id": job.icp_id,
            "account_match_id": job.account_match_id, "attempt_count": job.attempt_count,
            "created_at": job.created_at, "started_at": job.started_at,
            "finished_at": job.finished_at, "failure_detail": job.failure_detail,
            "workflow_id": job.workflow_id, "stage_key": job.stage_key,
            "deadline_at": job.deadline_at,
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
    elif isinstance(payload, (AccountResearchJobRequest, QualificationResearchJobRequest, DiscoveryResearchJobRequest)):
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


@router.post(
    "/icps",
    response_model=list[ResearchJobRecord],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_icp_jobs(
    payload: ICPResearchJobBatchRequest, session: SessionDependency
) -> list[ResearchJobRecord]:
    """Enqueue a bounded batch of ICP research jobs beneath a campaign."""
    _campaign_or_404(session, payload.campaign_id)
    jobs = enqueue_scoped_jobs(
        ResearchJobKind.ICP, payload.amount, session, campaign_id=payload.campaign_id
    )
    session.commit()
    return [_record(job) for job in jobs]


@router.post(
    "/accounts",
    response_model=list[ResearchJobRecord],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_account_jobs(
    payload: AccountResearchJobBatchRequest, session: SessionDependency
) -> list[ResearchJobRecord]:
    """Enqueue a bounded batch of account research jobs beneath an ICP."""
    _campaign_or_404(session, payload.campaign_id)
    _icp_or_404(session, payload.campaign_id, payload.icp_id)
    jobs = enqueue_scoped_jobs(
        ResearchJobKind.ACCOUNT,
        payload.amount,
        session,
        campaign_id=payload.campaign_id,
        icp_id=payload.icp_id,
    )
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


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str, session: SessionDependency) -> dict[str, object]:
    """Return workflow deadline, counters, and ordered stage outputs."""
    workflow = session.get(ResearchWorkflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    first_job = session.scalar(select(ResearchJob).where(ResearchJob.workflow_id == workflow_id))
    stages = list_job_stages(first_job.job_id, session)["items"] if first_job else []
    return {
        "workflow_id": workflow.workflow_id, "kind": workflow.kind,
        "status": workflow.status, "deadline_at": workflow.deadline_at,
        "llm_attempts": workflow.llm_attempts, "search_attempts": workflow.search_attempts,
        "fetch_attempts": workflow.fetch_attempts, "stages": stages,
    }
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

@router.get("/{job_id}/stages")
def list_job_stages(job_id: str, session: SessionDependency) -> dict[str, object]:
    """Return ordered immutable stage checkpoints for a job workflow."""
    job = _job_or_404(session, job_id)
    if not job.workflow_id:
        return {"workflow_id": None, "items": []}
    rows = session.scalars(
        select(ResearchStageResult)
        .where(ResearchStageResult.workflow_id == job.workflow_id)
        .order_by(ResearchStageResult.created_at, ResearchStageResult.stage_result_id)
    )
    return {
        "workflow_id": job.workflow_id,
        "items": [
            {
                "stage_result_id": row.stage_result_id,
                "stage_key": row.stage_key,
                "revision": row.revision,
                "outcome": row.outcome,
                "payload": row.payload,
                "unknowns": row.unknowns,
                "source_refs": row.source_refs,
                "created_at": row.created_at,
            }
            for row in rows
        ],
    }




@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_research_job(job_id: str, session: SessionDependency) -> None:
    """Delete a terminal job record without touching its research artifacts."""
    _job_or_404(session, job_id)
    if not delete_job(session, job_id):
        raise HTTPException(status_code=409, detail="job cannot be deleted")
    session.commit()
