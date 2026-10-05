"""Authenticated controller configuration and enablement routes."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from malg.api.dependencies import CrmClientDependency, SessionDependency
from malg.api.routers.jobs import admit_job
from malg.core.models.account_research_controller import (
    AccountResearchControllerConfiguration,
    AccountResearchControllerRecord,
    AccountResearchControllerState,
)
from malg.core.models.jobs import AccountResearchJobRequest
from malg.database.account_research_controller import (
    controller,
    matching_queued_count,
    matching_running_count,
)
from malg.database.models import AccountResearchController

router = APIRouter(
    prefix="/api/v1/account-research-controller", tags=["account-research-controller"]
)


def _record(row: AccountResearchController, session: Session) -> AccountResearchControllerRecord:
    """Return a status projection without exposing an internal lease token."""
    configuration = row.configuration
    return AccountResearchControllerRecord(
        enabled=row.enabled,
        revision=row.revision,
        configuration=AccountResearchControllerConfiguration.model_validate(
            {key: value for key, value in configuration.items() if key != "kind"}
        )
        if configuration
        else None,
        queued_matching_jobs=matching_queued_count(session, configuration) if configuration else 0,
        running_matching_jobs=matching_running_count(session, configuration)
        if configuration
        else 0,
        last_checked_at=row.last_checked_at,
        last_enqueued_job_id=row.last_enqueued_job_id,
        last_error=row.last_error,
        next_retry_at=row.next_retry_at,
    )


@router.get("", response_model=AccountResearchControllerRecord)
def get_controller(session: SessionDependency) -> AccountResearchControllerRecord:
    """Return the saved controller policy and matching queue state."""
    return _record(controller(session), session)


@router.put("/configuration", response_model=AccountResearchControllerRecord)
async def put_configuration(
    payload: AccountResearchControllerConfiguration,
    session: SessionDependency,
    client: CrmClientDependency,
) -> AccountResearchControllerRecord:
    """Validate and replace disabled controller configuration against live Twenty scope."""
    row = controller(session)
    if row.enabled:
        raise HTTPException(status_code=409, detail="controller_must_be_disabled")
    await admit_job(AccountResearchJobRequest.model_validate(payload.job_payload()), client)
    row.configuration, row.revision = payload.job_payload(), row.revision + 1
    row.last_error = row.next_retry_at = None
    session.commit()
    return _record(row, session)


@router.patch("/state", response_model=AccountResearchControllerRecord)
def set_controller_state(
    payload: AccountResearchControllerState, session: SessionDependency
) -> AccountResearchControllerRecord:
    """Persist an explicit enabled state and invalidate concurrent reconciliations."""
    row = controller(session)
    if row.revision != payload.revision:
        raise HTTPException(status_code=409, detail="controller_revision_conflict")
    if payload.enabled and row.configuration is None:
        raise HTTPException(status_code=409, detail="controller_configuration_required")
    row.enabled, row.revision = payload.enabled, row.revision + 1
    row.lease_token = row.lease_expires_at = None
    row.last_checked_at = datetime.now(UTC)
    session.commit()
    return _record(row, session)
