"""Typed records and response contracts for bounded operational statistics."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ToolSource = Literal["search", "fetch", "browser_mcp"]
ToolOutcome = Literal["success", "degraded", "blocked", "error", "rejected", "cancelled"]


class ToolRequestIssueRecord(BaseModel):
    """One sanitized provider diagnostic from an actual outbound request."""

    model_config = ConfigDict(frozen=True)
    occurred_at: datetime
    source: ToolSource
    engine: str | None = Field(default=None, max_length=80)
    category: Literal["block", "error"]
    message: str = Field(max_length=200)


class ToolRequestRecord(BaseModel):
    """Immutable terminal measurement of one logical tool invocation."""

    model_config = ConfigDict(frozen=True)
    request_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)
    job_id: str | None = None
    run_id: str | None = None
    worker_token: str | None = None
    source: ToolSource
    operation: str = Field(max_length=80)
    outcome: ToolOutcome
    cache_hit: bool = False
    coalesced: bool = False
    outbound_attempted: bool = False
    http_status: int | None = None
    reason_code: str | None = Field(default=None, max_length=80)
    result_count: int | None = Field(default=None, ge=0)
    issues: tuple[ToolRequestIssueRecord, ...] = ()


class StatsWindow(BaseModel):
    """Requested range and known collection coverage."""

    from_: datetime = Field(alias="from")
    to: datetime
    bucket_seconds: int
    generated_at: datetime
    collection_started_at: datetime
    tool_coverage_from: datetime
    retention_days: int = 90
    model_config = ConfigDict(populate_by_name=True)


class StatsExecution(BaseModel):
    """One worker or agent execution attempt."""

    run_id: str
    job_id: str | None
    worker_token: str | None
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "succeeded", "failed"]
    duration_seconds: float | None
    incomplete: bool


class StatsExecutionPage(BaseModel):
    """Bounded page of execution attempts."""

    total: int
    offset: int
    limit: int
    items: list[StatsExecution]
