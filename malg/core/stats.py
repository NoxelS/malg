"""Host-owned construction and best-effort persistence of tool statistics."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from malg.core.models.stats import (
    ToolOutcome,
    ToolRequestIssueRecord,
    ToolRequestRecord,
    ToolSource,
)


def begin_tool_request() -> tuple[str, datetime, int]:
    """Capture request identity, UTC start, and monotonic start without arguments."""
    return str(uuid.uuid4()), datetime.now(UTC), time.monotonic_ns()


def finish_tool_request(
    *,
    request_id: str,
    started_at: datetime,
    started_ns: int,
    source: ToolSource,
    operation: str,
    outcome: ToolOutcome,
    cache_hit: bool = False,
    coalesced: bool = False,
    outbound_attempted: bool = False,
    http_status: int | None = None,
    reason_code: str | None = None,
    result_count: int | None = None,
    issues: tuple[ToolRequestIssueRecord, ...] = (),
    recorder: Any = None,
) -> ToolRequestRecord:
    """Build and best-effort persist one terminal immutable measurement."""
    now = datetime.now(UTC)
    record = ToolRequestRecord(
        request_id=request_id,
        started_at=started_at,
        finished_at=now,
        duration_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
        source=source,
        operation=operation,
        outcome=outcome,
        cache_hit=cache_hit,
        coalesced=coalesced,
        outbound_attempted=outbound_attempted,
        http_status=http_status,
        reason_code=reason_code,
        result_count=result_count,
        issues=issues,
        job_id=getattr(recorder, "job_id", None),
        run_id=getattr(recorder, "run_id", None),
        worker_token=getattr(recorder, "worker_token", None),
    )
    try:
        if recorder is not None:
            from malg.database.stats import record_tool_request

            with recorder.session_factory.begin() as session:
                record_tool_request(session, record)
        else:
            from malg.core.agent_tracing import record_active_tool_request

            record_active_tool_request(record)
    except Exception:
        logging.getLogger(__name__).exception("tool statistics persistence failed")
    return record
