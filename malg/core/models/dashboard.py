"""Read-only contracts for operational dashboard data."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from malg.core.models.jobs import ResearchJobKind


class DashboardJobCounts(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0


class DashboardOutcomeCounts(BaseModel):
    """Completed jobs grouped by their business result rather than execution transport."""

    complete: int = 0
    partial: int = 0
    needs_review: int = 0
    insufficient_evidence: int = 0
    budget_exhausted: int = 0


class DashboardJobDuration(BaseModel):
    """Average execution time for one research job kind."""

    kind: ResearchJobKind
    average_duration_seconds: float | None


class DashboardSearchStatus(BaseModel):
    """Provider cooldown status; elapsed cooldown does not confirm recovery."""

    status: Literal["unknown", "paused", "retry_ready"] = "unknown"
    reason_code: str | None = None
    observed_at: datetime | None = None
    next_retry_at: datetime | None = None
    job_id: str | None = None


class DashboardSummary(BaseModel):
    """Local execution and worker state, excluding external CRM record counts."""

    active_workers: int
    jobs: DashboardJobCounts
    outcomes: DashboardOutcomeCounts
    job_durations: list[DashboardJobDuration]
    search: DashboardSearchStatus = Field(default_factory=DashboardSearchStatus)


class WorkerJobSummary(BaseModel):
    """Current worker ownership, including display-only historical job kinds."""

    job_id: str
    kind: str
    attempt_count: int
    claimed_at: datetime


class WorkerSummary(BaseModel):
    worker_id: str
    online_since: datetime
    last_seen_at: datetime
    status: Literal["idle", "running"]
    job: WorkerJobSummary | None = None


class WorkerOverviewPage(BaseModel):
    """One bounded page of workers for the operational dashboard."""

    items: list[WorkerSummary]
    total: int
    limit: int
    offset: int
