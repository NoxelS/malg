"""Read-only contracts for operational dashboard data."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from malg.core.models.jobs import ResearchJobKind


class DashboardJobCounts(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0


class DashboardJobDuration(BaseModel):
    """Average execution time for one research job kind."""

    kind: ResearchJobKind
    average_duration_seconds: float | None


class DashboardSummary(BaseModel):
    active_workers: int
    campaigns: int
    icps: int
    accounts: int
    jobs: DashboardJobCounts
    job_durations: list[DashboardJobDuration]


class WorkerJobSummary(BaseModel):
    job_id: str
    kind: ResearchJobKind
    attempt_count: int
    claimed_at: datetime


class WorkerSummary(BaseModel):
    worker_id: str
    online_since: datetime
    last_seen_at: datetime
    status: Literal["idle", "running"]
    job: WorkerJobSummary | None = None
