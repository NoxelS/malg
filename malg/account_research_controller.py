"""Supervised reconciliation for continuous account-research queue depth."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from malg.api.routers.jobs import _capture_contract, admit_job
from malg.core.models.jobs import AccountResearchJobRequest
from malg.crm.client import TwentyClient
from malg.database.account_research_controller import acquire_lease, matching_queued_count
from malg.database.jobs import enqueue_job
from malg.database.models import AccountResearchController


class AccountResearchQueueController:
    """Keep a single configured account-research request queued while enabled."""

    def __init__(self, sessions: sessionmaker[Session], crm_client: TwentyClient) -> None:
        self.sessions, self.crm_client = sessions, crm_client

    async def reconcile_once(self) -> None:
        """Atomically reserve, admit, and enqueue one job when the matching queue is empty."""
        now = datetime.now(UTC)
        with self.sessions.begin() as session:
            row, token = acquire_lease(session, now)
            if token is None or row.configuration is None:
                return
            configuration = dict(row.configuration)
            revision = row.revision
        try:
            request = AccountResearchJobRequest.model_validate(configuration)
            contract, _ = await admit_job(request, self.crm_client)
        except (HTTPException, ValueError) as error:
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            with self.sessions.begin() as session:
                locked_row = session.scalar(
                    select(AccountResearchController)
                    .where(AccountResearchController.controller_id == 1)
                    .with_for_update()
                )
                if locked_row and locked_row.lease_token == token:
                    locked_row.last_checked_at = now
                    locked_row.last_error = str(detail)[:2000]
                    locked_row.next_retry_at = now + timedelta(seconds=30)
                    locked_row.lease_token = locked_row.lease_expires_at = None
            return
        with self.sessions.begin() as session:
            locked_row = session.scalar(
                select(AccountResearchController)
                .where(AccountResearchController.controller_id == 1)
                .with_for_update()
            )
            if (
                not locked_row
                or not locked_row.enabled
                or locked_row.revision != revision
                or locked_row.lease_token != token
                or locked_row.configuration != configuration
            ):
                return
            locked_row.last_checked_at = now
            locked_row.lease_token = locked_row.lease_expires_at = None
            if matching_queued_count(session, configuration):
                locked_row.last_error = locked_row.next_retry_at = None
                return
            job = enqueue_job(request, session)
            _capture_contract(job, contract)
            locked_row.last_enqueued_job_id = job.job_id
            locked_row.last_error = locked_row.next_retry_at = None


async def controller_loop(controller: AccountResearchQueueController) -> None:
    """Reconcile continuously without letting a transient CRM error end supervision."""
    while True:
        with suppress(Exception):
            await controller.reconcile_once()
        await asyncio.sleep(5)
