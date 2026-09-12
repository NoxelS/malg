"""Read-only operational dashboard routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from malg.api.routers.artifacts import SessionDependency
from malg.core.models.dashboard import DashboardSummary, WorkerSummary
from malg.database.dashboard import get_dashboard_summary, list_active_workers


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

    return router
