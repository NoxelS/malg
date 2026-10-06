"""Bounded SQL aggregation for tool telemetry and worker-attempt history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import BigInteger, and_, case, cast, extract, func, literal, not_, or_, select
from sqlalchemy.orm import Session

from malg.database.models import (
    AgentRun,
    ResearchJob,
    StatsState,
    ToolRequestIssue,
    ToolRequestStat,
    WorkerHeartbeat,
)

_TOOL_SOURCES = ("search", "fetch", "browser_mcp")
_TOOL_OUTCOMES = ("success", "degraded", "blocked", "error", "rejected", "cancelled")
_TOOL_COUNTS = (
    "completed",
    "success",
    "degraded",
    "blocked",
    "error",
    "rejected",
    "cancelled",
    "cache_hits",
    "coalesced",
    "outbound_attempts",
)
_MAX_BUCKETS = 360


def bucket_seconds_for_hours(hours: int) -> int:
    """Return bucket width for a 1-2160 hour stats window."""
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 2160:
        raise ValueError("hours must be an integer between 1 and 2160")
    if hours <= 6:
        return 60
    if hours <= 48:
        return 600
    if hours <= 336:
        return 3600
    return 21600


def get_collection_started_at(session: Session) -> datetime | None:
    """Return the seeded telemetry coverage boundary, or ``None`` if unavailable."""
    value = session.scalar(select(StatsState.collection_started_at).where(StatsState.id == 1))
    return _as_utc(value) if value is not None else None


def aggregate_tool_requests(
    session: Session,
    *,
    from_at: datetime,
    to: datetime,
    tool_coverage_from: datetime,
    bucket_seconds: int,
) -> dict[str, Any]:
    """Aggregate retained terminal tool requests without loading raw telemetry rows.

    Bucket indexes are anchored to ``from_at`` and computed from integer UTC
    microseconds.  The returned ``available`` flag distinguishes uncovered
    history from a covered interval with no observations.
    ``tool_coverage_from`` is the later of collection start and the 90-day floor.
    """
    from_at = _as_utc(from_at)
    to = _as_utc(to)
    tool_coverage_from = _as_utc(tool_coverage_from)
    count = _bucket_count(from_at, to, bucket_seconds)
    width_micros = bucket_seconds * 1_000_000
    requested_buckets = _make_buckets(from_at, to, bucket_seconds, count)
    query_from = max(from_at, tool_coverage_from)
    available = query_from < to

    totals = {source: _empty_tool_counts() for source in _TOOL_SOURCES}
    if available:
        total_rows = session.execute(
            select(ToolRequestStat.source, *_tool_metric_columns())
            .where(
                ToolRequestStat.finished_at >= query_from,
                ToolRequestStat.finished_at < to,
                ToolRequestStat.source.in_(_TOOL_SOURCES),
            )
            .group_by(ToolRequestStat.source)
        )
        for row in total_rows:
            if row.source in totals:
                totals[row.source] = _read_tool_counts(row)

    first_covered_bucket = (
        max(0, (_utc_microseconds(query_from) - _utc_microseconds(from_at)) // width_micros)
        if available
        else count
    )
    buckets_by_source: dict[str, list[dict[str, Any]]] = {}
    for source in _TOOL_SOURCES:
        buckets_by_source[source] = [
            {
                "start": start,
                "end": end,
                **_empty_tool_counts(),
            }
            for start, end in requested_buckets[first_covered_bucket:]
        ]

    if available:
        bucket_index = _bucket_index_expression(
            ToolRequestStat.finished_at,
            session,
            from_at=from_at,
            bucket_seconds=bucket_seconds,
        )
        bucket_rows = session.execute(
            select(
                ToolRequestStat.source,
                bucket_index.label("bucket_index"),
                *_tool_metric_columns(),
            )
            .where(
                # Keep indexed timestamp predicates ahead of the bucket expression.
                ToolRequestStat.finished_at >= query_from,
                ToolRequestStat.finished_at < to,
                ToolRequestStat.source.in_(_TOOL_SOURCES),
            )
            .group_by(ToolRequestStat.source, bucket_index)
        )
        for row in bucket_rows:
            index = int(row.bucket_index)
            offset = index - first_covered_bucket
            source_buckets = buckets_by_source.get(row.source)
            if source_buckets is not None and 0 <= offset < len(source_buckets):
                source_buckets[offset].update(_read_tool_counts(row))

    return {
        "available": available,
        "totals": totals,
        "tools": [
            {"source": source, "totals": totals[source], "buckets": buckets_by_source[source]}
            for source in _TOOL_SOURCES
        ],
    }


def rank_tool_issues(
    session: Session,
    *,
    from_at: datetime,
    to: datetime,
    tool_coverage_from: datetime,
    ranking: Literal["blocks", "search"],
) -> dict[str, Any]:
    """Return a deterministic top-ten issue ranking and omitted occurrence count.

    ``blocks`` ranks all block-category issues. ``search`` ranks every search
    issue, including block and error diagnostics from otherwise degraded calls.
    ``tool_coverage_from`` is the later of collection start and the 90-day floor.
    """
    from_at = _as_utc(from_at)
    to = _as_utc(to)
    tool_coverage_from = _as_utc(tool_coverage_from)
    if ranking not in ("blocks", "search"):
        raise ValueError(f"unsupported issue ranking: {ranking}")
    query_from = max(from_at, tool_coverage_from)
    if query_from >= to:
        return {"items": [], "other_occurrences": 0}

    filters = [
        ToolRequestIssue.occurred_at >= query_from,
        ToolRequestIssue.occurred_at < to,
    ]
    if ranking == "blocks":
        filters.append(ToolRequestIssue.category == "block")
    else:
        filters.extend(
            (
                ToolRequestIssue.source == "search",
                ToolRequestIssue.engine.is_not(None),
                ToolRequestIssue.category.in_(("block", "error")),
            )
        )

    groups = (
        select(
            ToolRequestIssue.source.label("source"),
            ToolRequestIssue.engine.label("engine"),
            ToolRequestIssue.message.label("message"),
            func.count().label("occurrences"),
            func.count(func.distinct(ToolRequestIssue.request_id)).label("affected_requests"),
            func.max(ToolRequestIssue.occurred_at).label("last_seen"),
        )
        .where(*filters)
        .group_by(ToolRequestIssue.source, ToolRequestIssue.engine, ToolRequestIssue.message)
        .subquery()
    )
    engine_order = func.coalesce(groups.c.engine, "")
    ordering = (
        groups.c.occurrences.desc(),
        groups.c.source.asc(),
        engine_order.asc(),
        groups.c.message.asc(),
    )
    ranked = select(
        groups,
        func.row_number().over(order_by=ordering).label("rank_position"),
        func.sum(groups.c.occurrences).over().label("all_occurrences"),
    ).subquery()
    rows = session.execute(
        select(ranked)
        .where(ranked.c.rank_position <= 10)
        .order_by(
            ranked.c.occurrences.desc(),
            ranked.c.source.asc(),
            func.coalesce(ranked.c.engine, "").asc(),
            ranked.c.message.asc(),
        )
    ).all()
    if not rows:
        return {"items": [], "other_occurrences": 0}

    items: list[dict[str, Any]] = []
    top_occurrences = 0
    all_occurrences = int(rows[0].all_occurrences)
    for row in rows:
        occurrences = int(row.occurrences)
        top_occurrences += occurrences
        items.append(
            {
                "source": row.source,
                "engine": row.engine,
                "message": row.message,
                "occurrences": occurrences,
                "affected_requests": int(row.affected_requests),
                "last_seen": _as_utc(row.last_seen),
            }
        )
    return {
        "items": items,
        "other_occurrences": max(0, all_occurrences - top_occurrences),
    }


def aggregate_worker_history(
    session: Session,
    *,
    from_at: datetime,
    to: datetime,
    generated_at: datetime,
    heartbeat_timeout_seconds: int,
    bucket_seconds: int,
    worker_offset: int = 0,
    worker_limit: int = 25,
    execution_offset: int = 0,
    execution_limit: int = 50,
) -> dict[str, Any]:
    """Aggregate worker-scope attempts and page historical workers/executions.

    Mutable job state is consulted only to verify a currently open worker span;
    attempt starts/completions and terminal execution status always come from
    immutable ``AgentRun`` rows.
    """
    from_at = _as_utc(from_at)
    to = _as_utc(to)
    generated_at = _as_utc(generated_at)
    if (
        isinstance(heartbeat_timeout_seconds, bool)
        or not isinstance(heartbeat_timeout_seconds, int)
        or heartbeat_timeout_seconds < 1
    ):
        raise ValueError("heartbeat_timeout_seconds must be a positive integer")
    if (
        isinstance(worker_offset, bool)
        or not isinstance(worker_offset, int)
        or worker_offset < 0
        or isinstance(worker_limit, bool)
        or not isinstance(worker_limit, int)
        or not 1 <= worker_limit <= 25
    ):
        raise ValueError("worker pagination must use offset >= 0 and limit 1-25")
    if (
        isinstance(execution_offset, bool)
        or not isinstance(execution_offset, int)
        or execution_offset < 0
        or isinstance(execution_limit, bool)
        or not isinstance(execution_limit, int)
        or not 1 <= execution_limit <= 100
    ):
        raise ValueError("execution pagination must use offset >= 0 and limit 1-100")
    count = _bucket_count(from_at, to, bucket_seconds)
    buckets = _make_buckets(from_at, to, bucket_seconds, count)
    active_end = min(to, generated_at)
    heartbeat_since = generated_at - timedelta(seconds=heartbeat_timeout_seconds)

    run = AgentRun
    job = ResearchJob
    heartbeat = WorkerHeartbeat
    joined = run.__table__.outerjoin(job.__table__, job.job_id == run.job_id).outerjoin(
        heartbeat.__table__, heartbeat.worker_token == run.worker_token
    )
    verified_claim = and_(
        run.scope == "worker",
        run.status == "running",
        run.finished_at.is_(None),
        run.worker_token.is_not(None),
        job.status == "running",
        job.owner_worker_token == run.worker_token,
        job.claim_token.is_not(None),
        job.claimed_at <= run.started_at,
        job.claim_expires_at > generated_at,
        heartbeat.last_seen_at >= heartbeat_since,
    )
    terminal_span = and_(
        run.scope == "worker",
        run.status.in_(("succeeded", "failed")),
        run.finished_at.is_not(None),
        run.finished_at >= run.started_at,
        run.started_at < to,
        or_(
            run.finished_at > from_at,
            and_(run.finished_at == run.started_at, run.started_at >= from_at),
        ),
    )
    verified_active_span = (
        and_(verified_claim, run.started_at < active_end)
        if active_end > from_at
        else literal(False)
    )
    stale_unfinished_start = and_(
        run.scope == "worker",
        run.finished_at.is_(None),
        run.started_at >= from_at,
        run.started_at < to,
        not_(func.coalesce(verified_claim, literal(False))),
    )
    included = or_(terminal_span, verified_active_span, stale_unfinished_start)

    attempts = _aggregate_attempt_buckets(
        session,
        from_at=from_at,
        to=to,
        bucket_seconds=bucket_seconds,
    )
    incomplete_runs = int(
        session.scalar(
            select(func.count()).select_from(joined).where(included, run.finished_at.is_(None))
        )
        or 0
    )

    worker_count = int(
        session.scalar(
            select(func.count(func.distinct(run.worker_token)))
            .select_from(joined)
            .where(included, run.worker_token.is_not(None))
        )
        or 0
    )
    worker_tokens = [
        token
        for token in session.scalars(
            select(run.worker_token)
            .select_from(joined)
            .where(included, run.worker_token.is_not(None))
            .group_by(run.worker_token)
            .order_by(run.worker_token.asc())
            .offset(worker_offset)
            .limit(worker_limit)
        ).all()
        if token is not None
    ]
    worker_stats: dict[str, dict[str, Any]] = {}
    for token in worker_tokens:
        worker_stats[token] = {
            "worker_token": token,
            "buckets": [
                {
                    "start": start,
                    "end": end,
                    "busy_seconds": 0.0,
                    "incomplete_runs": 0,
                }
                for start, end in buckets
            ],
        }
    busy_micros = {token: [0] * count for token in worker_tokens}
    _accumulate_worker_intervals(
        session,
        joined=joined,
        included=included,
        verified_claim=verified_claim,
        from_at=from_at,
        to=to,
        active_end=active_end,
        bucket_seconds=bucket_seconds,
        buckets=buckets,
        worker_tokens=worker_tokens,
        worker_stats=worker_stats,
        busy_micros=busy_micros,
    )
    for token, per_bucket in busy_micros.items():
        for index, microseconds in enumerate(per_bucket):
            worker_stats[token]["buckets"][index]["busy_seconds"] = microseconds / 1_000_000

    execution_total = int(
        session.scalar(select(func.count()).select_from(joined).where(included)) or 0
    )
    execution_rows = session.execute(
        select(
            run.run_id,
            run.job_id,
            run.worker_token,
            run.started_at,
            run.finished_at,
            run.status,
        )
        .select_from(joined)
        .where(included)
        .order_by(run.started_at.desc(), run.run_id.asc())
        .offset(execution_offset)
        .limit(execution_limit)
    ).all()
    executions: list[dict[str, Any]] = []
    for row in execution_rows:
        started_at = _as_utc(row.started_at)
        finished_at = _as_utc(row.finished_at) if row.finished_at is not None else None
        executions.append(
            {
                "run_id": row.run_id,
                "job_id": row.job_id,
                "worker_token": row.worker_token,
                "started_at": started_at,
                "finished_at": finished_at,
                "status": row.status,
                "duration_seconds": (
                    (finished_at - started_at).total_seconds() if finished_at is not None else None
                ),
                "incomplete": finished_at is None,
            }
        )

    return {
        "jobs": {"buckets": attempts, "incomplete_runs": incomplete_runs},
        "workers": {
            "total": worker_count,
            "offset": worker_offset,
            "limit": worker_limit,
            "items": [worker_stats[token] for token in worker_tokens],
        },
        "executions": {
            "total": execution_total,
            "offset": execution_offset,
            "limit": execution_limit,
            "items": executions,
        },
    }


def _aggregate_attempt_buckets(
    session: Session,
    *,
    from_at: datetime,
    to: datetime,
    bucket_seconds: int,
) -> list[dict[str, Any]]:
    """Count worker attempt starts and terminal completions by event time."""
    count = _bucket_count(from_at, to, bucket_seconds)
    buckets = [
        {
            "start": start,
            "end": end,
            "attempts_started": 0,
            "attempts_succeeded": 0,
            "attempts_failed": 0,
        }
        for start, end in _make_buckets(from_at, to, bucket_seconds, count)
    ]
    start_index = _bucket_index_expression(
        AgentRun.started_at,
        session,
        from_at=from_at,
        bucket_seconds=bucket_seconds,
    )
    starts = session.execute(
        select(start_index.label("bucket_index"), func.count().label("amount"))
        .where(
            AgentRun.scope == "worker",
            AgentRun.started_at >= from_at,
            AgentRun.started_at < to,
        )
        .group_by(start_index)
    )
    for row in starts:
        buckets[int(row.bucket_index)]["attempts_started"] = int(row.amount)

    finish_index = _bucket_index_expression(
        AgentRun.finished_at,
        session,
        from_at=from_at,
        bucket_seconds=bucket_seconds,
    )
    finishes = session.execute(
        select(
            finish_index.label("bucket_index"),
            AgentRun.status,
            func.count().label("amount"),
        )
        .where(
            AgentRun.scope == "worker",
            AgentRun.finished_at >= from_at,
            AgentRun.finished_at < to,
            AgentRun.status.in_(("succeeded", "failed")),
        )
        .group_by(finish_index, AgentRun.status)
    )
    for row in finishes:
        key = "attempts_succeeded" if row.status == "succeeded" else "attempts_failed"
        buckets[int(row.bucket_index)][key] = int(row.amount)
    return buckets


def _accumulate_worker_intervals(
    session: Session,
    *,
    joined: Any,
    included: Any,
    verified_claim: Any,
    from_at: datetime,
    to: datetime,
    active_end: datetime,
    bucket_seconds: int,
    buckets: list[tuple[datetime, datetime]],
    worker_tokens: list[str],
    worker_stats: dict[str, dict[str, Any]],
    busy_micros: dict[str, list[int]],
) -> None:
    """Stream selected attempts, union busy intervals, and mark incomplete buckets."""
    if not worker_tokens:
        return
    run = AgentRun
    rows = session.execute(
        select(
            run.worker_token,
            run.run_id,
            run.started_at,
            run.finished_at,
            run.status,
            verified_claim.label("verified_claim"),
        )
        .select_from(joined)
        .where(included, run.worker_token.in_(worker_tokens))
        .order_by(run.worker_token.asc(), run.started_at.asc(), run.run_id.asc())
        .execution_options(yield_per=512)
    )

    current_worker: str | None = None
    merged_start: datetime | None = None
    merged_end: datetime | None = None

    def flush_interval() -> None:
        if current_worker is not None and merged_start is not None and merged_end is not None:
            _add_busy_interval(
                busy_micros[current_worker],
                merged_start,
                merged_end,
                from_at=from_at,
                bucket_seconds=bucket_seconds,
                buckets=buckets,
            )

    for row in rows:
        worker_token = row.worker_token
        if worker_token != current_worker:
            flush_interval()
            current_worker = worker_token
            merged_start = None
            merged_end = None

        started_at = _as_utc(row.started_at)
        finished_at = _as_utc(row.finished_at) if row.finished_at is not None else None
        verified = bool(row.verified_claim)
        interval: tuple[datetime, datetime] | None = None
        if (
            finished_at is not None
            and row.status in ("succeeded", "failed")
            and finished_at > started_at
        ):
            interval = (max(started_at, from_at), min(finished_at, to))
        elif (
            finished_at is None and row.status == "running" and verified and active_end > started_at
        ):
            interval = (max(started_at, from_at), min(active_end, to))

        if finished_at is None:
            if interval is not None and interval[0] < interval[1]:
                for index in _overlapped_bucket_indexes(
                    interval[0], interval[1], from_at, bucket_seconds, len(buckets)
                ):
                    worker_stats[worker_token]["buckets"][index]["incomplete_runs"] += 1
            elif from_at <= started_at < to and not verified:
                index = (_utc_microseconds(started_at) - _utc_microseconds(from_at)) // (
                    bucket_seconds * 1_000_000
                )
                worker_stats[worker_token]["buckets"][index]["incomplete_runs"] += 1

        if interval is None or interval[0] >= interval[1]:
            continue
        start, end = interval
        if merged_start is None:
            merged_start, merged_end = start, end
        elif merged_end is not None and start <= merged_end:
            if end > merged_end:
                merged_end = end
        else:
            flush_interval()
            merged_start, merged_end = start, end
    flush_interval()


def _add_busy_interval(
    amounts: list[int],
    start: datetime,
    end: datetime,
    *,
    from_at: datetime,
    bucket_seconds: int,
    buckets: list[tuple[datetime, datetime]],
) -> None:
    """Add one already-unioned span to exact bucket intersections."""
    if start >= end:
        return
    for index in _overlapped_bucket_indexes(start, end, from_at, bucket_seconds, len(buckets)):
        bucket_start, bucket_end = buckets[index]
        overlap_start = max(start, bucket_start)
        overlap_end = min(end, bucket_end)
        if overlap_start < overlap_end:
            amounts[index] += _utc_microseconds(overlap_end) - _utc_microseconds(overlap_start)


def _overlapped_bucket_indexes(
    start: datetime,
    end: datetime,
    from_at: datetime,
    bucket_seconds: int,
    bucket_count: int,
) -> range:
    """Return bucket indexes intersecting a half-open microsecond interval."""
    if start >= end or bucket_count <= 0:
        return range(0)
    width = bucket_seconds * 1_000_000
    first = max(0, (_utc_microseconds(start) - _utc_microseconds(from_at)) // width)
    end_delta = _utc_microseconds(end) - _utc_microseconds(from_at)
    last = min(bucket_count - 1, (end_delta - 1) // width)
    return range(first, last + 1) if first <= last else range(0)


def _tool_metric_columns() -> list[Any]:
    """Build conditional SQL counts for the shared tool outcome dimensions."""
    outcome_counts = [
        func.coalesce(func.sum(case((ToolRequestStat.outcome == outcome, 1), else_=0)), 0).label(
            outcome
        )
        for outcome in _TOOL_OUTCOMES
    ]
    return [
        func.count().label("completed"),
        *outcome_counts,
        func.coalesce(func.sum(case((ToolRequestStat.cache_hit.is_(True), 1), else_=0)), 0).label(
            "cache_hits"
        ),
        func.coalesce(func.sum(case((ToolRequestStat.coalesced.is_(True), 1), else_=0)), 0).label(
            "coalesced"
        ),
        func.coalesce(
            func.sum(case((ToolRequestStat.outbound_attempted.is_(True), 1), else_=0)), 0
        ).label("outbound_attempts"),
    ]


def _empty_tool_counts() -> dict[str, int]:
    return {key: 0 for key in _TOOL_COUNTS}


def _read_tool_counts(row: Any) -> dict[str, int]:
    return {key: int(getattr(row, key) or 0) for key in _TOOL_COUNTS}


def _bucket_index_expression(
    column: Any,
    session: Session,
    *,
    from_at: datetime,
    bucket_seconds: int,
) -> Any:
    """Compute floor((event UTC microseconds - anchor) / width) exactly."""
    dialect_name = session.get_bind().dialect.name
    epoch_microseconds: Any
    if dialect_name == "postgresql":
        epoch_microseconds = cast(extract("epoch", column) * 1_000_000, BigInteger)
    elif dialect_name == "sqlite":
        whole_seconds = cast(func.strftime("%s", func.substr(column, 1, 19)), BigInteger)
        fractional_microseconds = func.coalesce(cast(func.substr(column, 21, 6), BigInteger), 0)
        epoch_microseconds = whole_seconds * 1_000_000 + fractional_microseconds
    else:
        raise ValueError(f"unsupported stats query dialect: {dialect_name}")
    delta = epoch_microseconds - literal(_utc_microseconds(from_at), type_=BigInteger())
    width = literal(bucket_seconds * 1_000_000, type_=BigInteger())
    # All callers apply a >= anchor timestamp predicate, so integer division is floor.
    return delta.self_group().op("/")(width)


def _make_buckets(
    from_at: datetime,
    to: datetime,
    bucket_seconds: int,
    count: int,
) -> list[tuple[datetime, datetime]]:
    width = timedelta(seconds=bucket_seconds)
    return [
        (start, min(start + width, to))
        for index in range(count)
        if (start := from_at + index * width) < to
    ]


def _bucket_count(from_at: datetime, to: datetime, bucket_seconds: int) -> int:
    if (
        isinstance(bucket_seconds, bool)
        or not isinstance(bucket_seconds, int)
        or bucket_seconds <= 0
    ):
        raise ValueError("bucket_seconds must be a positive integer")
    duration = _utc_microseconds(to) - _utc_microseconds(from_at)
    if duration <= 0:
        raise ValueError("stats window must have positive duration")
    width = bucket_seconds * 1_000_000
    count = (duration + width - 1) // width
    if count > _MAX_BUCKETS:
        raise ValueError(f"stats window exceeds the {_MAX_BUCKETS}-bucket limit")
    return count


def _utc_microseconds(value: datetime) -> int:
    value = _as_utc(value)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _as_utc(value: datetime) -> datetime:
    """Normalize aware values and SQLite's naive UTC result values at the boundary."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
