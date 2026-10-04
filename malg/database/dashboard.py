"""Read-only aggregate and active-worker dashboard queries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from malg.core.models.dashboard import (
    DashboardJobCounts,
    DashboardJobDuration,
    DashboardOutcomeCounts,
    DashboardSummary,
    WorkerJobSummary,
    WorkerOverviewPage,
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

    outcomes = {
        outcome: 0
        for outcome in (
            "complete",
            "partial",
            "needs_review",
            "insufficient_evidence",
            "budget_exhausted",
        )
    }
    for outcome, count in session.execute(
        select(ResearchJob.result_outcome, func.count())
        .where(ResearchJob.status == ResearchJobStatus.SUCCEEDED.value)
        .group_by(ResearchJob.result_outcome)
    ):
        if outcome in outcomes:
            outcomes[outcome] = count

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
        outcomes=DashboardOutcomeCounts(**outcomes),
        job_durations=job_durations,
    )


def list_active_workers(session: Session, active_since: datetime) -> list[WorkerSummary]:
    """List all fresh workers with their current running claim, if any."""
    return _select_workers(session, active_since)


def list_worker_overview(
    session: Session,
    active_since: datetime,
    *,
    limit: int,
    offset: int,
    sort: str,
    direction: str,
) -> WorkerOverviewPage:
    """Return one bounded page of fresh workers in a deterministic order."""
    total = session.scalar(
        select(func.count())
        .select_from(WorkerHeartbeat)
        .where(WorkerHeartbeat.last_seen_at >= active_since)
    ) or 0
    return WorkerOverviewPage(
        items=_select_workers(
            session,
            active_since,
            limit=limit,
            offset=offset,
            sort=sort,
            direction=direction,
        ),
        total=total,
        limit=limit,
        offset=offset,
    )


def _select_workers(
    session: Session,
    active_since: datetime,
    *,
    limit: int | None = None,
    offset: int = 0,
    sort: str = "status",
    direction: str = "asc",
) -> list[WorkerSummary]:
    """Build fresh worker rows, applying only known ordering columns."""
    statement = (
        select(WorkerHeartbeat, ResearchJob)
        .outerjoin(
            ResearchJob,
            (ResearchJob.owner_worker_token == WorkerHeartbeat.worker_token)
            & (ResearchJob.status == "running"),
        )
        .where(WorkerHeartbeat.last_seen_at >= active_since)
    )
    sort_columns = {
        "status": case((ResearchJob.status == "running", 0), else_=1),
        "last_seen_at": WorkerHeartbeat.last_seen_at,
        "online_since": WorkerHeartbeat.created_at,
        "claimed_at": ResearchJob.claimed_at,
        "kind": ResearchJob.kind,
        "attempt_count": ResearchJob.attempt_count,
    }
    sort_column = sort_columns.get(sort, WorkerHeartbeat.last_seen_at)
    ordering = sort_column.asc() if direction == "asc" else sort_column.desc()
    statement = statement.order_by(
        ordering,
        case((ResearchJob.status == "running", 0), else_=1),
        WorkerHeartbeat.last_seen_at.desc(),
        WorkerHeartbeat.worker_token,
    )
    if limit is not None:
        statement = statement.offset(offset).limit(limit)
    rows = session.execute(statement).all()
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
    return workers
