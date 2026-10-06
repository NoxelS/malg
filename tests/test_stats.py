"""Durable tool telemetry persistence and pruning behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from malg.core.models.stats import ToolRequestIssueRecord, ToolRequestRecord
from malg.database.models import Base, ToolRequestIssue, ToolRequestStat
from malg.database.stats import prune_tool_requests, record_tool_request


def test_duplicate_terminal_request_and_issue_are_not_counted_twice() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    stat = ToolRequestRecord(
        request_id="terminal-once",
        started_at=now,
        finished_at=now,
        duration_ms=0,
        source="search",
        operation="search",
        outcome="degraded",
        issues=(
            ToolRequestIssueRecord(
                occurred_at=now, source="search", category="error", message="Other provider error"
            ),
        ),
    )
    with Session(engine) as session, session.begin():
        record_tool_request(session, stat)
        record_tool_request(session, stat)
    with Session(engine) as session:
        assert len(session.scalars(select(ToolRequestStat)).all()) == 1
        assert len(session.scalars(select(ToolRequestIssue)).all()) == 1


def test_pruning_deletes_only_expired_tool_rows_and_issues() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        for key, finished in (
            ("old", now - timedelta(days=91)),
            ("recent", now - timedelta(days=89)),
        ):
            record_tool_request(
                session,
                ToolRequestRecord(
                    request_id=key,
                    started_at=finished,
                    finished_at=finished,
                    duration_ms=1,
                    source="fetch",
                    operation="fetch",
                    outcome="success",
                ),
            )
        assert prune_tool_requests(session, before=now - timedelta(days=90)) == 1
    with Session(engine) as session:
        assert [row.request_id for row in session.scalars(select(ToolRequestStat)).all()] == [
            "recent"
        ]
