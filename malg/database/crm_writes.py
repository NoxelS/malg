"""Durable, idempotent write intents for remote CRM mutations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from malg.database.jobs import require_claim
from malg.database.models import CrmWriteOperation, ResearchJob


class CrmWriteConflict(RuntimeError):
    """An immutable write identity was reused with different intent metadata."""


def _same_intent(
    existing: CrmWriteOperation,
    *,
    contract_version: int,
    contract_hash: str,
    intended_fields: Mapping[str, object],
    observed_record_version: str | None,
) -> bool:
    return (
        existing.contract_version == contract_version
        and existing.contract_hash == contract_hash
        and existing.intended_fields == dict(intended_fields)
        and existing.observed_record_version == observed_record_version
    )


def _load_existing(
    session: Session, job: str, stage_key: str, object_name: str, record: str, field_key: str
) -> CrmWriteOperation | None:
    return session.scalar(
        select(CrmWriteOperation).where(
            CrmWriteOperation.job_id == job,
            CrmWriteOperation.stage_key == stage_key,
            CrmWriteOperation.object_name == object_name,
            CrmWriteOperation.record_id == record,
            CrmWriteOperation.field_key == field_key,
        )
    )


def prepare_write(
    session: Session,
    *,
    job_id: UUID | str,
    workflow_id: UUID | str | None,
    stage_key: str,
    object_name: str,
    record_id: UUID | str,
    field_key: str,
    contract_version: int,
    contract_hash: str,
    intended_fields: Mapping[str, object],
    observed_record_version: str | None,
    claim_token: str,
) -> CrmWriteOperation:
    """Create or reuse one immutable write intent under a live claim.

    The savepoint handles two workers racing the unique operation identity.  The
    loser reloads the winner's immutable row rather than manufacturing a new key.
    """
    job = str(job_id)
    record = str(record_id)
    require_claim(session, job, claim_token, datetime.now(UTC))
    existing = _load_existing(session, job, stage_key, object_name, record, field_key)
    if existing is not None:
        if not _same_intent(
            existing,
            contract_version=contract_version,
            contract_hash=contract_hash,
            intended_fields=intended_fields,
            observed_record_version=observed_record_version,
        ):
            raise CrmWriteConflict(
                "Existing CRM intent differs from the requested immutable payload."
            )
        return existing
    operation = CrmWriteOperation(
        operation_id=str(uuid4()),
        job_id=job,
        workflow_id=str(workflow_id) if workflow_id is not None else None,
        stable_operation_key=f"{job}:{stage_key}:{object_name}:{record}:{field_key}",
        stage_key=stage_key,
        object_name=object_name,
        record_id=record,
        field_key=field_key,
        contract_version=contract_version,
        contract_hash=contract_hash,
        intended_fields=dict(intended_fields),
        observed_record_version=observed_record_version,
        status="prepared",
    )
    try:
        with session.begin_nested():
            session.add(operation)
            session.flush()
    except IntegrityError as error:
        raced = _load_existing(session, job, stage_key, object_name, record, field_key)
        if raced is None:
            raise
        operation = raced
        if not _same_intent(
            operation,
            contract_version=contract_version,
            contract_hash=contract_hash,
            intended_fields=intended_fields,
            observed_record_version=observed_record_version,
        ):
            raise CrmWriteConflict(
                "Existing CRM intent differs from the requested immutable payload."
            ) from error
    return operation


def record_mutation_attempt(
    session: Session, operation: CrmWriteOperation, *, claim_token: str
) -> None:
    """Count an HTTP mutation attempt, fenced to the operation's owning job."""
    require_claim(session, operation.job_id, claim_token, datetime.now(UTC))
    session.refresh(operation, with_for_update=True)
    if operation.status in {"confirmed", "conflict", "cancelled"}:
        raise CrmWriteConflict("Terminal CRM operations cannot launch mutations.")
    operation.status = "prepared"
    operation.attempt_count += 1
    session.flush()


def mark_write(
    session: Session,
    operation: CrmWriteOperation,
    *,
    status: str,
    claim_token: str,
    error: str | None = None,
) -> None:
    """Record a sanitized remote outcome under the owning live claim."""
    if status not in {"prepared", "confirmed", "conflict", "failed", "cancelled"}:
        raise ValueError(f"Unsupported CRM write status: {status}")
    require_claim(session, operation.job_id, claim_token, datetime.now(UTC))
    session.refresh(operation, with_for_update=True)
    if operation.status in {"confirmed", "conflict", "cancelled"} and status != operation.status:
        raise CrmWriteConflict("Terminal CRM operations cannot be reopened.")
    operation.status = status
    operation.sanitized_error = error
    session.flush()


def reconcile_write(
    session: Session,
    operation: CrmWriteOperation,
    *,
    status: str,
    error: str | None = None,
) -> None:
    """Record an observed remote effect without claiming or mutating CRM."""
    if status not in {"confirmed", "conflict", "cancelled"}:
        raise ValueError("Reconciliation only resolves observed terminal outcomes.")
    job = session.scalar(
        select(ResearchJob).where(ResearchJob.job_id == operation.job_id).with_for_update()
    )
    session.refresh(operation, with_for_update=True)
    if status == "cancelled":
        active = (
            job is not None
            and job.status == "running"
            and job.claim_expires_at is not None
            and (
                job.claim_expires_at.replace(tzinfo=UTC)
                if job.claim_expires_at.tzinfo is None
                else job.claim_expires_at
            )
            > datetime.now(UTC)
        )
        if operation.attempt_count or active:
            raise CrmWriteConflict(
                "Issued or actively claimed intents cannot be cancelled by reconciliation."
            )
    if operation.status not in {"prepared", "failed"}:
        return
    operation.status = status
    operation.sanitized_error = error
    session.flush()
