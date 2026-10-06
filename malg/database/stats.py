"""Durable persistence helpers for tool-request telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from malg.core.models.stats import ToolRequestRecord
from malg.database.models import ToolRequestIssue, ToolRequestStat


def record_tool_request(session: Session, stat: ToolRequestRecord) -> None:
    """Persist a terminal request and its diagnostics atomically; duplicates are no-ops."""
    values = stat.model_dump(exclude={"issues"})
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    result = cast(
        CursorResult[Any],
        session.execute(
            insert(ToolRequestStat)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["request_id"])
        ),
    )
    if not result.rowcount:
        return
    unique_issues = {
        (issue.source, issue.engine or "", issue.message): issue for issue in stat.issues
    }
    session.add_all(
        ToolRequestIssue(
            request_id=stat.request_id,
            occurred_at=issue.occurred_at,
            source=issue.source,
            engine=issue.engine,
            category=issue.category,
            message=issue.message,
        )
        for issue in unique_issues.values()
    )


def prune_tool_requests(session: Session, *, before: datetime, limit: int = 5000) -> int:
    """Delete one bounded oldest-first batch and its diagnostics in caller transaction."""
    ids = session.scalars(
        select(ToolRequestStat.request_id)
        .where(ToolRequestStat.finished_at < before)
        .order_by(ToolRequestStat.finished_at, ToolRequestStat.request_id)
        .limit(limit)
    ).all()
    if not ids:
        return 0
    session.execute(delete(ToolRequestIssue).where(ToolRequestIssue.request_id.in_(ids)))
    result = cast(
        CursorResult[Any],
        session.execute(delete(ToolRequestStat).where(ToolRequestStat.request_id.in_(ids))),
    )
    return result.rowcount or 0
