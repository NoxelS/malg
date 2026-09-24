"""Versioned API routes for enqueueing and managing research jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from malg.api.dependencies import CrmClientDependency, SessionDependency
from malg.core.models.jobs import (
    AccountResearchJobBatchRequest,
    AccountResearchJobRequest,
    CampaignResearchJobBatchRequest,
    CampaignResearchJobRequest,
    ICPResearchJobBatchRequest,
    ICPResearchJobRequest,
    ResearchJobKind,
    ResearchJobRecord,
    ResearchJobRequest,
    ResearchJobStatus,
)
from malg.crm.client import (
    TwentyClient,
    TwentyConflict,
    TwentyError,
    TwentyRecordMissing,
    TwentySchemaIncompatible,
)
from malg.crm.schema import SchemaConflict
from malg.database.jobs import (
    cancel_job,
    delete_job,
    enqueue_campaign_jobs,
    enqueue_job,
    enqueue_scoped_jobs,
    retry_failed_job,
)
from malg.database.models import (
    CrmWriteOperation,
    ResearchJob,
    ResearchStageResult,
    ResearchWorkflow,
)

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
            "account_id": job.account_id,
            "person_id": job.person_id,
            "request_payload": job.request_payload,
            "contract_version": job.contract_version,
            "contract_hash": job.contract_hash,
            "result_refs": job.result_refs,
            "result_outcome": job.result_outcome,
            "data_origin": job.data_origin,
            "attempt_count": job.attempt_count,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "failure_detail": job.failure_detail,
            "workflow_id": job.workflow_id,
            "stage_key": job.stage_key,
            "deadline_at": job.deadline_at,
        }
    )


def _job_or_404(session: Session, job_id: str) -> ResearchJob:
    """Return one job or the stable not-found response."""
    job = session.get(ResearchJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


async def _admit(
    payload: ResearchJobRequest, client: TwentyClient
) -> tuple[dict[str, object], dict[str, object]]:
    """Verify actual metadata and parent relationships before any local insert."""
    inputs: dict[str, object]
    try:
        contract = await client.check_contract()
        if not contract["schema_compatible"]:
            raise HTTPException(status_code=503, detail="crm_schema_incompatible")
        records = await client.read_scope(payload.model_dump(mode="json"))
        inputs = {scope: record.targeting_snapshot() for scope, record in records.items()}
    except TwentyRecordMissing as error:
        raise HTTPException(status_code=404, detail="crm_record_missing") from error
    except TwentyConflict as error:
        raise HTTPException(status_code=409, detail="crm_scope_conflict") from error
    except SchemaConflict as error:
        raise HTTPException(status_code=503, detail="crm_schema_incompatible") from error
    except TwentyError as error:
        reason = (
            "crm_schema_incompatible"
            if isinstance(error, TwentySchemaIncompatible)
            else "crm_unavailable"
        )
        raise HTTPException(status_code=503, detail=reason) from error
    return contract, inputs


def _capture_contract(job: ResearchJob, contract: dict[str, object]) -> None:
    """Persist the observed admission contract, not merely the desired manifest."""
    job.contract_version = int(str(contract["contract_version"]))
    job.contract_hash = str(contract["contract_hash"])


@router.post("", response_model=ResearchJobRecord, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    payload: ResearchJobRequest, session: SessionDependency, client: CrmClientDependency
) -> ResearchJobRecord:
    """Reject unavailable or invalid remote scope before enqueue."""
    contract, _ = await _admit(payload, client)
    job = enqueue_job(payload, session)
    _capture_contract(job, contract)
    session.commit()
    return _record(job)


@router.post(
    "/campaigns",
    response_model=list[ResearchJobRecord],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_campaign_jobs(
    payload: CampaignResearchJobBatchRequest,
    session: SessionDependency,
    client: CrmClientDependency,
) -> list[ResearchJobRecord]:
    """Enqueue a bounded batch of independent campaign research jobs."""
    contract, _ = await _admit(CampaignResearchJobRequest(), client)
    jobs = enqueue_campaign_jobs(payload.amount, session)
    for job in jobs:
        _capture_contract(job, contract)
    session.commit()
    return [_record(job) for job in jobs]


@router.post(
    "/icps",
    response_model=list[ResearchJobRecord],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_icp_jobs(
    payload: ICPResearchJobBatchRequest, session: SessionDependency, client: CrmClientDependency
) -> list[ResearchJobRecord]:
    """Enqueue a bounded batch of ICP jobs for a remote campaign reference."""
    contract, _ = await _admit(ICPResearchJobRequest(campaign_id=payload.campaign_id), client)
    jobs = enqueue_scoped_jobs(
        ResearchJobKind.ICP, payload.amount, session, campaign_id=str(payload.campaign_id)
    )
    for job in jobs:
        _capture_contract(job, contract)
    session.commit()
    return [_record(job) for job in jobs]


@router.post(
    "/accounts",
    response_model=list[ResearchJobRecord],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_account_jobs(
    payload: AccountResearchJobBatchRequest, session: SessionDependency, client: CrmClientDependency
) -> list[ResearchJobRecord]:
    """Enqueue a bounded batch of account jobs for remote campaign and ICP references."""
    contract, _ = await _admit(
        AccountResearchJobRequest(campaign_id=payload.campaign_id, icp_id=payload.icp_id), client
    )
    jobs = enqueue_scoped_jobs(
        ResearchJobKind.ACCOUNT,
        payload.amount,
        session,
        campaign_id=str(payload.campaign_id) if payload.campaign_id else None,
        icp_id=str(payload.icp_id),
    )
    for job in jobs:
        _capture_contract(job, contract)
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
        "workflow_id": workflow.workflow_id,
        "kind": workflow.kind,
        "status": workflow.status,
        "deadline_at": workflow.deadline_at,
        "llm_attempts": workflow.llm_attempts,
        "search_attempts": workflow.search_attempts,
        "fetch_attempts": workflow.fetch_attempts,
        "stages": stages,
    }


@router.post(
    "/{job_id}/retry", response_model=ResearchJobRecord, status_code=status.HTTP_202_ACCEPTED
)
async def retry_research_job(
    job_id: str, session: SessionDependency, client: CrmClientDependency
) -> ResearchJobRecord:
    """Requeue compatible failed Twenty work without changing operation identities."""
    existing = _job_or_404(session, job_id)
    if existing.status != "failed" or existing.data_origin != "twenty":
        raise HTTPException(status_code=409, detail="job_not_retryable")
    try:
        payload: ResearchJobRequest = TypeAdapter(ResearchJobRequest).validate_python(
            existing.request_payload
        )
    except ValidationError as error:
        raise HTTPException(status_code=409, detail="crm_input_changed") from error
    contract, inputs = await _admit(payload, client)
    if (
        existing.contract_version != contract["contract_version"]
        or existing.contract_hash != contract["contract_hash"]
    ):
        raise HTTPException(status_code=409, detail="crm_input_changed")
    workflow = session.get(ResearchWorkflow, existing.workflow_id) if existing.workflow_id else None
    if workflow and workflow.input_payload.get("crm_inputs") != inputs:
        raise HTTPException(status_code=409, detail="crm_input_changed")
    job = retry_failed_job(session, job_id, datetime.now(UTC))
    if job is None:
        raise HTTPException(status_code=409, detail="only failed jobs can be retried")
    session.commit()
    return _record(job)


@router.get("/{job_id}", response_model=ResearchJobRecord)
def get_job(job_id: str, session: SessionDependency) -> ResearchJobRecord:
    """Return one durable job record."""
    return _record(_job_or_404(session, job_id))


@router.post("/{job_id}/cancel", response_model=ResearchJobRecord)
def cancel_research_job(job_id: str, session: SessionDependency) -> ResearchJobRecord:
    """Clear queued or running ownership; worker supervision stops stale work."""
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
        return {"workflow_id": None, "input_payload": None, "items": []}
    rows = session.scalars(
        select(ResearchStageResult)
        .where(ResearchStageResult.workflow_id == job.workflow_id)
        .order_by(ResearchStageResult.created_at, ResearchStageResult.stage_result_id)
    )
    workflow = session.get(ResearchWorkflow, job.workflow_id)
    return {
        "workflow_id": job.workflow_id,
        "input_payload": workflow.input_payload if workflow is not None else None,
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


@router.get("/{job_id}/writes")
def list_job_writes(job_id: str, session: SessionDependency) -> dict[str, object]:
    """Expose durable write state without querying CRM or leaking claim tokens."""
    _job_or_404(session, job_id)
    rows = list(
        session.scalars(
            select(CrmWriteOperation)
            .where(CrmWriteOperation.job_id == job_id)
            .order_by(CrmWriteOperation.created_at, CrmWriteOperation.operation_id)
        )
    )
    return {
        "items": [
            {
                "operation_id": row.operation_id,
                "stage_key": row.stage_key,
                "object_name": row.object_name,
                "record_id": row.record_id,
                "field_key": row.field_key,
                "status": row.status,
                "attempt_count": row.attempt_count,
                "intended_fields": row.intended_fields,
                "observed_record_version": row.observed_record_version,
                "sanitized_error": row.sanitized_error,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ],
        "side_effects_pending": any(row.status == "prepared" for row in rows),
    }


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_research_job(job_id: str, session: SessionDependency) -> None:
    """Delete a terminal job record without touching its research artifacts."""
    _job_or_404(session, job_id)
    if not delete_job(session, job_id):
        raise HTTPException(status_code=409, detail="job cannot be deleted")
    session.commit()
