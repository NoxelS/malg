"""Authenticated read-only agent trace routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import Session

from malg.api.dependencies import SessionDependency
from malg.core.models.traces import (
    AgentRunRecord,
    AgentTraceEventPage,
    AgentTraceEventRecord,
    AgentTurnRecord,
)
from malg.database.traces import get_run, job_exists, list_events, list_runs, list_turns

router = APIRouter(tags=["agent-traces"])


def _run(run: Any) -> AgentRunRecord:
    return AgentRunRecord.model_validate(run, from_attributes=True)


def _event(event: Any) -> AgentTraceEventRecord:
    return AgentTraceEventRecord.model_validate(event, from_attributes=True)


def _turn(turn: Any) -> AgentTurnRecord:
    return AgentTurnRecord.model_validate(turn, from_attributes=True)


def _owned_run(session: Session, run_id: str) -> Any:
    run = get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="agent run not found")
    return run


@router.get("/api/v1/jobs/{job_id}/agent-runs", response_model=list[AgentRunRecord])
def job_runs(job_id: str, session: SessionDependency) -> list[AgentRunRecord]:
    """List all trace scopes belonging to a job."""
    if not job_exists(session, job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return [_run(item) for item in list_runs(session, job_id)]


@router.get("/api/v1/agent-runs/{run_id}", response_model=AgentRunRecord)
def one_run(run_id: str, session: SessionDependency) -> AgentRunRecord:
    """Return one trace scope."""
    return _run(_owned_run(session, run_id))


@router.get("/api/v1/agent-runs/{run_id}/turns", response_model=list[AgentTurnRecord])
def run_turns(run_id: str, session: SessionDependency) -> list[AgentTurnRecord]:
    """Return LLM attempts scoped to one trace run."""
    _owned_run(session, run_id)
    return [_turn(item) for item in list_turns(session, run_id)]


@router.get("/api/v1/agent-runs/{run_id}/events", response_model=AgentTraceEventPage)
def run_events(
    run_id: str,
    session: SessionDependency,
    after_event_id: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> AgentTraceEventPage:
    """Return an exclusive cursor page of raw ordered events."""
    _owned_run(session, run_id)
    events = list_events(session, run_id, after_event_id, limit)
    return AgentTraceEventPage(
        items=[_event(item) for item in events],
        next_after_event_id=events[-1].event_id if events else after_event_id,
        limit=limit,
    )
