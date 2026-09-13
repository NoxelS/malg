"""API contracts for authenticated agent trace retrieval."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentTraceEventRecord(BaseModel):
    """One ordered trace journal record."""

    event_id: int
    run_id: str
    turn_id: int | None
    sequence: int
    occurred_at: datetime
    event_type: str
    payload: dict[str, Any]


class AgentTurnRecord(BaseModel):
    """One LLM request attempt and response."""

    turn_id: int
    run_id: str
    generation_id: str
    turn_number: int
    method_name: str
    strategy: str
    started_at: datetime
    finished_at: datetime | None
    success: bool | None
    error_type: str | None
    error_message: str | None
    error_traceback: str | None
    request_messages: list[dict[str, Any]]
    request_params: dict[str, Any]
    response: dict[str, Any] | None


class AgentRunRecord(BaseModel):
    """One worker or generated-agent execution scope."""

    run_id: str
    job_id: str
    scope: Literal["worker", "agent"]
    worker_token: str | None
    agent_name: str
    method_name: str
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None
    error_type: str | None
    error_message: str | None
    error_traceback: str | None


class AgentTraceEventPage(BaseModel):
    """A bounded page of ordered trace events."""

    items: list[AgentTraceEventRecord]
    next_after_event_id: int | None = None
    limit: int = Field(ge=1, le=500)
