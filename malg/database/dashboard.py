"""Read-only aggregate and active-worker dashboard queries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from malg.core.models.dashboard import (
    DashboardJobCounts,
    DashboardSummary,
    WorkerJobSummary,
    WorkerSummary,
)
from malg.database.models import ICP, Account, Campaign, ResearchJob, WorkerHeartbeat


def get_dashboard_summary(session: Session, active_since: datetime) -> DashboardSummary:
    """Return canonical artifact, worker, and all lifecycle job totals."""
    counts = {status: 0 for status in ("queued", "running", "succeeded", "failed", "cancelled")}
    for status, count in session.execute(
        select(ResearchJob.status, func.count()).group_by(ResearchJob.status)
    ):
        if status in counts:
            counts[status] = count
    return DashboardSummary(
        active_workers=session.scalar(
            select(func.count())
            .select_from(WorkerHeartbeat)
            .where(WorkerHeartbeat.last_seen_at >= active_since)
        )
        or 0,
        campaigns=session.scalar(select(func.count()).select_from(Campaign)) or 0,
        icps=session.scalar(select(func.count()).select_from(ICP)) or 0,
        accounts=session.scalar(select(func.count()).select_from(Account)) or 0,
        jobs=DashboardJobCounts(**counts),
    )


def list_active_workers(session: Session, active_since: datetime) -> list[WorkerSummary]:
    """List fresh workers with their current running claim, if any."""
    rows = session.execute(
        select(WorkerHeartbeat, ResearchJob)
        .outerjoin(
            ResearchJob,
            (ResearchJob.claim_token == WorkerHeartbeat.worker_token)
            & (ResearchJob.status == "running"),
        )
        .where(WorkerHeartbeat.last_seen_at >= active_since)
    ).all()
    workers: list[WorkerSummary] = []
    for heartbeat, job in rows:
        workers.append(
            WorkerSummary(
                worker_id=heartbeat.worker_token,
                online_since=heartbeat.created_at,
                last_seen_at=heartbeat.last_seen_at,
                status="running" if job is not None else "idle",
                job=(
                    WorkerJobSummary(
                        job_id=job.job_id,
                        kind=job.kind,
                        attempt_count=job.attempt_count,
                        claimed_at=job.claimed_at,
                    )
                    if job is not None and job.claimed_at is not None
                    else None
                ),
            )
        )
    workers.sort(
        key=lambda worker: (
            worker.status != "running",
            -worker.last_seen_at.timestamp(),
            worker.worker_id,
        )
    )
    return workers
