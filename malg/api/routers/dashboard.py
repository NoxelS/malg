"""Read-only operational dashboard routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from malg.api.dependencies import SessionDependency
from malg.core.models.dashboard import DashboardSummary, WorkerOverviewPage, WorkerSummary
from malg.database.dashboard import get_dashboard_summary, list_active_workers, list_worker_overview


def dashboard_router(active_worker_timeout_seconds: int) -> APIRouter:
    """Build dashboard routes using the startup-captured liveness timeout."""
    router = APIRouter(prefix="/api/v1", tags=["dashboard"])

    def active_since() -> datetime:
        return datetime.now(UTC) - timedelta(seconds=active_worker_timeout_seconds)

    @router.get("/dashboard", response_model=DashboardSummary)
    def dashboard(session: SessionDependency) -> DashboardSummary:
        return get_dashboard_summary(session, active_since())

    @router.get("/workers", response_model=list[WorkerSummary])
    def workers(session: SessionDependency) -> list[WorkerSummary]:
        return list_active_workers(session, active_since())

    @router.get("/workers/overview", response_model=WorkerOverviewPage)
    def worker_overview(
        session: SessionDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        sort: Annotated[
            Literal[
                "status", "last_seen_at", "online_since", "claimed_at", "kind", "attempt_count"
            ],
            Query(),
        ] = "status",
        direction: Literal["asc", "desc"] = "asc",
    ) -> WorkerOverviewPage:
        """Return one bounded page of fresh worker records."""
        return list_worker_overview(
            session,
            active_since(),
            limit=limit,
            offset=offset,
            sort=sort,
            direction=direction,
        )

    return router
