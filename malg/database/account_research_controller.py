"""Transactional persistence for the singleton account-research controller."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from malg.database.models import AccountResearchController, ResearchJob


def controller(session: Session) -> AccountResearchController:
    """Return the singleton policy row, creating it disabled when absent."""
    row = session.get(AccountResearchController, 1)
    if row is None:
        row = AccountResearchController(controller_id=1)
        session.add(row)
        session.flush()
    return row


def matching_queued_count(session: Session, configuration: dict[str, object]) -> int:
    """Count queued account jobs whose immutable request exactly matches policy."""
    rows = session.scalars(
        select(ResearchJob.request_payload).where(
            ResearchJob.status == "queued",
            ResearchJob.kind == "account",
            ResearchJob.icp_id == str(configuration["icp_id"]),
        )
    )
    return sum(1 for payload in rows if payload == configuration)


def matching_running_count(session: Session, configuration: dict[str, object]) -> int:
    """Count currently running jobs matching the configured request."""
    rows = session.scalars(
        select(ResearchJob.request_payload).where(
            ResearchJob.status == "running",
            ResearchJob.kind == "account",
            ResearchJob.icp_id == str(configuration["icp_id"]),
        )
    )
    return sum(1 for payload in rows if payload == configuration)


def acquire_lease(session: Session, now: datetime) -> tuple[AccountResearchController, str | None]:
    """Lock policy state and reserve a reconciliation only when replenishment is due."""
    row = session.scalar(
        select(AccountResearchController)
        .where(AccountResearchController.controller_id == 1)
        .with_for_update()
    )
    if row is None:
        row = controller(session)
        return row, None
    if (
        not row.enabled
        or row.configuration is None
        or (row.next_retry_at and row.next_retry_at > now)
    ):
        return row, None
    if row.lease_expires_at and row.lease_expires_at > now:
        return row, None
    if matching_queued_count(session, row.configuration):
        row.last_checked_at, row.last_error = now, None
        return row, None
    token = str(uuid4())
    row.lease_token, row.lease_expires_at = token, now + timedelta(seconds=30)
    return row, token
