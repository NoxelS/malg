"""Authenticated bounded statistics and worker execution history endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from malg.api.dependencies import SessionDependency
from malg.database.stats_queries import (
    aggregate_tool_requests,
    aggregate_worker_history,
    bucket_seconds_for_hours,
    get_collection_started_at,
    rank_tool_issues,
)


def stats_router(active_worker_timeout_seconds: int) -> APIRouter:
    """Create Stats endpoints using startup-captured worker liveness policy."""
    router = APIRouter(prefix="/api/v1/stats", tags=["stats"])

    def build_range(
        hours: int, requested_to: datetime | None, generated_at: datetime
    ) -> tuple[datetime, datetime, int]:
        if requested_to is not None:
            if requested_to.tzinfo is None or requested_to.utcoffset() is None:
                raise HTTPException(status_code=422, detail="to must include a timezone")
            requested_to = requested_to.astimezone(UTC)
            if requested_to > generated_at or requested_to < generated_at - timedelta(days=90):
                raise HTTPException(
                    status_code=422, detail="to is outside the available retention window"
                )
        else:
            requested_to = generated_at
        return requested_to - timedelta(hours=hours), requested_to, bucket_seconds_for_hours(hours)

    @router.get("")
    def stats(
        session: SessionDependency,
        hours: Annotated[int, Query(ge=1, le=2160)] = 24,
        to: datetime | None = None,
        worker_offset: Annotated[int, Query(ge=0)] = 0,
        worker_limit: Annotated[int, Query(ge=1, le=25)] = 25,
    ) -> dict[str, Any]:
        """Return bounded tool, issue, job, and worker aggregates for one range."""
        generated_at = datetime.now(UTC)
        from_at, range_to, bucket_seconds = build_range(hours, to, generated_at)
        collection_started_at = get_collection_started_at(session)
        if collection_started_at is None:
            raise HTTPException(status_code=503, detail="stats_state_unavailable")
        tool_coverage_from = max(collection_started_at, generated_at - timedelta(days=90))
        tools = aggregate_tool_requests(
            session,
            from_at=from_at,
            to=range_to,
            tool_coverage_from=tool_coverage_from,
            bucket_seconds=bucket_seconds,
        )
        issues = rank_tool_issues(
            session,
            from_at=from_at,
            to=range_to,
            tool_coverage_from=tool_coverage_from,
            ranking="blocks",
        )
        search_issues = rank_tool_issues(
            session,
            from_at=from_at,
            to=range_to,
            tool_coverage_from=tool_coverage_from,
            ranking="search",
        )
        worker = aggregate_worker_history(
            session,
            from_at=from_at,
            to=range_to,
            generated_at=generated_at,
            heartbeat_timeout_seconds=active_worker_timeout_seconds,
            bucket_seconds=bucket_seconds,
            worker_offset=worker_offset,
            worker_limit=worker_limit,
            execution_limit=50,
        )
        return {
            "window": {
                "from": from_at,
                "to": range_to,
                "bucket_seconds": bucket_seconds,
                "generated_at": generated_at,
                "collection_started_at": collection_started_at,
                "tool_coverage_from": tool_coverage_from,
                "retention_days": 90,
            },
            "tools": tools["tools"],
            "block_reasons": issues,
            "searxng_errors": search_issues,
            "jobs": worker["jobs"],
            "workers": worker["workers"],
        }

    @router.get("/executions")
    def executions(
        session: SessionDependency,
        hours: Annotated[int, Query(ge=1, le=2160)] = 24,
        to: datetime | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        """Return a stable page of worker execution attempts for the same range."""
        generated_at = datetime.now(UTC)
        if get_collection_started_at(session) is None:
            raise HTTPException(status_code=503, detail="stats_state_unavailable")
        from_at, range_to, bucket_seconds = build_range(hours, to, generated_at)
        history = aggregate_worker_history(
            session,
            from_at=from_at,
            to=range_to,
            generated_at=generated_at,
            heartbeat_timeout_seconds=active_worker_timeout_seconds,
            bucket_seconds=bucket_seconds,
            execution_offset=offset,
            execution_limit=limit,
        )
        page = history["executions"]
        return {"total": page["total"], "offset": offset, "limit": limit, "items": page["items"]}

    return router
