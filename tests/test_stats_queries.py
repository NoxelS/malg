"""Focused database contracts for bounded stats aggregation."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from malg.database.models import (
    AgentRun,
    Base,
    ResearchJob,
    ToolRequestIssue,
    ToolRequestStat,
    WorkerHeartbeat,
)
from malg.database.stats_queries import (
    aggregate_tool_requests,
    aggregate_worker_history,
    bucket_seconds_for_hours,
    rank_tool_issues,
)


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _request(
    request_id: str,
    finished_at: datetime,
    *,
    source: str = "search",
    outcome: str = "success",
    cache_hit: bool = False,
    coalesced: bool = False,
    outbound_attempted: bool = False,
) -> ToolRequestStat:
    return ToolRequestStat(
        request_id=request_id,
        started_at=finished_at - timedelta(seconds=1),
        finished_at=finished_at,
        duration_ms=100,
        source=source,
        operation="test",
        outcome=outcome,
        cache_hit=cache_hit,
        coalesced=coalesced,
        outbound_attempted=outbound_attempted,
    )


def _job(job_id: str, *, status: str = "succeeded", **values) -> ResearchJob:
    return ResearchJob(job_id=job_id, kind="account_research", status=status, **values)


def _run(
    run_id: str,
    job_id: str,
    started_at: datetime,
    *,
    status: str = "succeeded",
    finished_at: datetime | None = None,
    worker_token: str | None = None,
    scope: str = "worker",
) -> AgentRun:
    return AgentRun(
        run_id=run_id,
        job_id=job_id,
        scope=scope,
        worker_token=worker_token,
        agent_name="test",
        method_name="run",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
    )


def test_tool_aggregation_uses_exact_half_open_microsecond_buckets() -> None:
    engine = _engine()
    start = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    rows = [
        _request("before", start - timedelta(microseconds=1)),
        _request("search-start", start, outbound_attempted=True),
        _request(
            "search-left-edge",
            start + timedelta(seconds=60, microseconds=-1),
            outcome="degraded",
            cache_hit=True,
            coalesced=True,
        ),
        _request(
            "fetch-right-edge",
            start + timedelta(seconds=60),
            source="fetch",
            outcome="blocked",
            cache_hit=True,
            outbound_attempted=True,
        ),
        _request(
            "mcp-last-microsecond",
            end - timedelta(microseconds=1),
            source="browser_mcp",
            outcome="error",
        ),
        _request("at-to", end, source="fetch"),
    ]
    issues = [
        ToolRequestIssue(
            request_id="search-start",
            occurred_at=start,
            source="search",
            engine="alpha",
            category="block",
            message="CAPTCHA",
        ),
        ToolRequestIssue(
            request_id="search-left-edge",
            occurred_at=start + timedelta(seconds=1),
            source="search",
            engine="alpha",
            category="error",
            message="CAPTCHA",
        ),
        ToolRequestIssue(
            request_id="fetch-right-edge",
            occurred_at=start + timedelta(seconds=60),
            source="fetch",
            engine=None,
            category="block",
            message="HTTP 429",
        ),
        ToolRequestIssue(
            request_id="at-to",
            occurred_at=end,
            source="search",
            engine="outside",
            category="error",
            message="outside window",
        ),
    ]
    with Session(engine) as session:
        session.add_all([*rows, *issues])
        session.commit()

        result = aggregate_tool_requests(
            session,
            from_at=start,
            to=end,
            tool_coverage_from=start,
            bucket_seconds=60,
        )
        by_source = {item["source"]: item for item in result["tools"]}
        assert tuple(by_source) == ("search", "fetch", "browser_mcp")
        assert by_source["search"]["totals"]["completed"] == 2
        assert by_source["search"]["buckets"][0]["completed"] == 2
        assert by_source["search"]["buckets"][1]["completed"] == 0
        assert by_source["fetch"]["totals"]["blocked"] == 1
        assert by_source["fetch"]["buckets"][1]["blocked"] == 1
        assert by_source["browser_mcp"]["buckets"][1]["error"] == 1
        assert by_source["search"]["totals"]["cache_hits"] == 1
        assert by_source["search"]["totals"]["coalesced"] == 1
        assert by_source["search"]["totals"]["outbound_attempts"] == 1

        partial = aggregate_tool_requests(
            session,
            from_at=start,
            to=end,
            tool_coverage_from=start + timedelta(microseconds=1),
            bucket_seconds=60,
        )
        assert partial["available"] is True
        assert partial["tools"][0]["buckets"][0]["start"] == start
        assert partial["tools"][0]["totals"]["completed"] == 1
        unavailable = aggregate_tool_requests(
            session,
            from_at=start,
            to=end,
            tool_coverage_from=end,
            bucket_seconds=60,
        )
        assert unavailable["available"] is False
        assert unavailable["tools"][0]["buckets"] == []

        blocks = rank_tool_issues(
            session,
            from_at=start,
            to=end,
            tool_coverage_from=start,
            ranking="blocks",
        )
        search = rank_tool_issues(
            session,
            from_at=start,
            to=end,
            tool_coverage_from=start,
            ranking="search",
        )
        assert [
            (item["source"], item["engine"], item["occurrences"]) for item in blocks["items"]
        ] == [
            ("fetch", None, 1),
            ("search", "alpha", 1),
        ]
        assert search["items"] == [
            {
                "source": "search",
                "engine": "alpha",
                "message": "CAPTCHA",
                "occurrences": 2,
                "affected_requests": 2,
                "last_seen": start + timedelta(seconds=1),
            }
        ]
    engine.dispose()


def test_worker_history_unions_busy_spans_and_keeps_stale_attempts_unknown() -> None:
    engine = _engine()
    start = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    generated = start + timedelta(minutes=30)
    terminal_one_end = start + timedelta(minutes=40)
    terminal_two_start = start + timedelta(minutes=20)
    terminal_two_end = start + timedelta(minutes=50)
    stale_start = start + timedelta(minutes=15)
    active_start = start - timedelta(minutes=10)
    with Session(engine) as session:
        session.add_all(
            [
                _job("terminal-one"),
                _job("terminal-two"),
                _job("stale", status="queued"),
                _job(
                    "active",
                    status="running",
                    owner_worker_token="worker-z",
                    claim_token="claim-z",
                    claimed_at=active_start - timedelta(minutes=5),
                    claim_expires_at=generated + timedelta(minutes=10),
                ),
                _job("nested", status="succeeded"),
                _job("old-stale", status="queued"),
            ]
        )
        session.add_all(
            [
                _run(
                    "terminal-one-run",
                    "terminal-one",
                    start,
                    finished_at=terminal_one_end,
                    worker_token="worker-a",
                ),
                _run(
                    "terminal-two-run",
                    "terminal-two",
                    terminal_two_start,
                    status="failed",
                    finished_at=terminal_two_end,
                    worker_token="worker-a",
                ),
                _run(
                    "stale-run",
                    "stale",
                    stale_start,
                    status="running",
                    worker_token="worker-b",
                ),
                _run(
                    "active-run",
                    "active",
                    active_start,
                    status="running",
                    worker_token="worker-z",
                ),
                _run(
                    "nested-run",
                    "nested",
                    start,
                    finished_at=start + timedelta(minutes=10),
                    worker_token="worker-ignored",
                    scope="agent",
                ),
                _run(
                    "old-stale-run",
                    "old-stale",
                    start - timedelta(hours=1),
                    status="running",
                    worker_token="worker-old",
                ),
                WorkerHeartbeat(
                    worker_token="worker-z",
                    created_at=active_start,
                    last_seen_at=generated,
                ),
            ]
        )
        session.commit()

        result = aggregate_worker_history(
            session,
            from_at=start,
            to=end,
            generated_at=generated,
            heartbeat_timeout_seconds=60,
            bucket_seconds=3600,
        )
        jobs = result["jobs"]
        assert jobs["incomplete_runs"] == 2
        assert jobs["buckets"] == [
            {
                "start": start,
                "end": end,
                "attempts_started": 3,
                "attempts_succeeded": 1,
                "attempts_failed": 1,
            }
        ]
        workers = result["workers"]
        assert workers["total"] == 3
        assert [item["worker_token"] for item in workers["items"]] == [
            "worker-a",
            "worker-b",
            "worker-z",
        ]
        assert workers["items"][0]["buckets"][0]["busy_seconds"] == 3000
        assert workers["items"][1]["buckets"][0]["busy_seconds"] == 0
        assert workers["items"][1]["buckets"][0]["incomplete_runs"] == 1
        assert workers["items"][2]["buckets"][0]["busy_seconds"] == 1800
        assert workers["items"][2]["buckets"][0]["incomplete_runs"] == 1
        executions = result["executions"]
        assert executions["total"] == 4
        assert all(
            item["incomplete"] for item in executions["items"] if item["finished_at"] is None
        )
        assert all(
            item["duration_seconds"] is None for item in executions["items"] if item["incomplete"]
        )
    engine.dispose()


def test_stats_bucket_width_thresholds() -> None:
    assert [bucket_seconds_for_hours(hours) for hours in (1, 6, 7, 48, 49, 336, 337, 2160)] == [
        60,
        60,
        600,
        600,
        3600,
        3600,
        21600,
        21600,
    ]


def test_issue_ranking_is_stable_and_reports_omitted_occurrences() -> None:
    engine = _engine()
    start = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all(
            [
                _request(
                    f"request-{index:02d}",
                    start + timedelta(seconds=index),
                    outcome="degraded",
                )
                for index in range(11)
            ]
        )
        session.add_all(
            [
                ToolRequestIssue(
                    request_id=f"request-{index:02d}",
                    occurred_at=start + timedelta(seconds=index),
                    source="search",
                    engine=f"engine-{index:02d}",
                    category="error",
                    message="rate limit",
                )
                for index in range(11)
            ]
        )
        session.commit()

        ranking = rank_tool_issues(
            session,
            from_at=start,
            to=start + timedelta(minutes=1),
            tool_coverage_from=start,
            ranking="search",
        )

        assert [item["engine"] for item in ranking["items"]] == [
            f"engine-{index:02d}" for index in range(10)
        ]
        assert all(item["affected_requests"] == 1 for item in ranking["items"])
        assert ranking["other_occurrences"] == 1
    engine.dispose()


def test_postgres_tool_buckets_preserve_microsecond_edges() -> None:
    """Exercise PostgreSQL epoch extraction against exact half-open bucket edges."""
    database_url = os.environ.get("STATS_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("STATS_TEST_POSTGRES_URL must name a disposable PostgreSQL database")
    schema = f"stats_test_{uuid4().hex}"
    admin = create_engine(database_url)
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
        Base.metadata.create_all(
            engine,
            tables=[
                ResearchJob.__table__,
                AgentRun.__table__,
                WorkerHeartbeat.__table__,
                ToolRequestStat.__table__,
                ToolRequestIssue.__table__,
            ],
        )
        start = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
        with Session(engine) as session, session.begin():
            session.add_all(
                [
                    _request("pg-left", start + timedelta(seconds=60, microseconds=-1)),
                    _request("pg-edge", start + timedelta(seconds=60)),
                ]
            )
        with Session(engine) as session:
            report = aggregate_tool_requests(
                session,
                from_at=start,
                to=start + timedelta(minutes=2),
                tool_coverage_from=start,
                bucket_seconds=60,
            )
            search = next(item for item in report["tools"] if item["source"] == "search")
            assert [bucket["completed"] for bucket in search["buckets"]] == [1, 1]
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
