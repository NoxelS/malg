"""Read-only aggregate and active-worker dashboard queries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from malg.core.models.dashboard import (
    DashboardJobCounts,
    DashboardJobDuration,
    DashboardSummary,
    WorkerJobSummary,
    WorkerSummary,
)
from malg.core.models.jobs import ResearchJobKind, ResearchJobStatus
from malg.database.models import ResearchJob, WorkerHeartbeat


def get_dashboard_summary(session: Session, active_since: datetime) -> DashboardSummary:
    """Return local worker and job aggregates without a CRM data mirror."""
    counts = {status: 0 for status in ("queued", "running", "succeeded", "failed", "cancelled")}
    for status, count in session.execute(
        select(ResearchJob.status, func.count()).group_by(ResearchJob.status)
    ):
        if status in counts:
            counts[status] = count

    duration_totals = {kind: [0.0, 0] for kind in ResearchJobKind}
    for kind, started_at, finished_at in session.execute(
        select(ResearchJob.kind, ResearchJob.started_at, ResearchJob.finished_at).where(
            ResearchJob.status == ResearchJobStatus.SUCCEEDED.value,
            ResearchJob.started_at.is_not(None),
            ResearchJob.finished_at.is_not(None),
        )
    ):
        duration = (finished_at - started_at).total_seconds()
        if duration < 0:
            continue
        if kind in duration_totals:
            duration_totals[kind][0] += duration
            duration_totals[kind][1] += 1
    job_durations = [
        DashboardJobDuration(
            kind=kind,
            average_duration_seconds=(total / count if count else None),
        )
        for kind, (total, count) in duration_totals.items()
    ]

    return DashboardSummary(
        active_workers=session.scalar(
            select(func.count())
            .select_from(WorkerHeartbeat)
            .where(WorkerHeartbeat.last_seen_at >= active_since)
        )
        or 0,
        jobs=DashboardJobCounts(**counts),
        job_durations=job_durations,
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
