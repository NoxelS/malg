"""Transactional persistence operations for durable agent traces."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from malg.database.models import AgentRun, AgentTraceEvent, AgentTurn, ResearchJob


def create_run(
    session: Session,
    job_id: str,
    scope: str,
    agent_name: str,
    method_name: str,
    worker_token: str | None = None,
    run_id: str | None = None,
) -> AgentRun:
    """Create a running trace scope."""
    run = AgentRun(
        run_id=run_id or str(uuid4()),
        job_id=job_id,
        scope=scope,
        worker_token=worker_token,
        agent_name=agent_name,
        method_name=method_name,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    return run


def finalize_run(
    session: Session, run_id: str, success: bool, error: BaseException | None = None
) -> None:
    """Close a trace scope with complete diagnostics."""
    run = session.get(AgentRun, run_id)
    if run is None:
        return
    run.status = "succeeded" if success else "failed"
    run.finished_at = datetime.now(UTC)
    if error is not None:
        import traceback

        run.error_type = type(error).__name__
        run.error_message = str(error)
        run.error_traceback = "".join(traceback.format_exception(error))


def create_turn(
    session: Session,
    run_id: str,
    generation_id: str,
    turn_number: int,
    method_name: str,
    strategy: str,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
) -> AgentTurn:
    """Persist a request attempt."""
    turn = AgentTurn(
        run_id=run_id,
        generation_id=generation_id,
        turn_number=turn_number,
        method_name=method_name,
        strategy=strategy,
        started_at=datetime.now(UTC),
        request_messages=messages,
        request_params=params,
    )
    session.add(turn)
    session.flush()
    return turn


def finalize_turn(
    session: Session,
    turn_id: int,
    response: Any = None,
    success: bool = True,
    error: BaseException | None = None,
) -> None:
    """Close a request attempt."""
    turn = session.get(AgentTurn, turn_id)
    if turn is None:
        return
    turn.finished_at = datetime.now(UTC)
    turn.success = success
    if response is not None:
        turn.response = normalize_json(response)
    if error is not None:
        import traceback

        turn.error_type = type(error).__name__
        turn.error_message = str(error)
        turn.error_traceback = "".join(traceback.format_exception(error))


def normalize_json(value: Any) -> Any:
    """Recursively retain JSON-compatible values and represent unknown objects."""
    if hasattr(value, "model_dump"):
        return normalize_json(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: normalize_json(getattr(value, field.name)) for field in fields(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): normalize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize_json(item) for item in value]
    return repr(value)


def append_event(
    session: Session, run_id: str, event_type: str, payload: Any, turn_id: int | None = None
) -> AgentTraceEvent:
    """Append the next ordered event in a run."""
    sequence = (
        session.scalar(
            select(AgentTraceEvent.sequence)
            .where(AgentTraceEvent.run_id == run_id)
            .order_by(AgentTraceEvent.sequence.desc())
            .limit(1)
        )
        or 0
    )
    event = AgentTraceEvent(
        run_id=run_id,
        turn_id=turn_id,
        sequence=sequence + 1,
        occurred_at=datetime.now(UTC),
        event_type=event_type,
        payload=normalize_json(payload),
    )
    session.add(event)
    session.flush()
    return event


def list_runs(session: Session, job_id: str) -> list[AgentRun]:
    """List runs for a job newest first."""
    return list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.job_id == job_id)
            .order_by(AgentRun.started_at.desc(), AgentRun.run_id.desc())
        )
    )


def get_run(session: Session, run_id: str) -> AgentRun | None:
    """Fetch one run."""
    return session.get(AgentRun, run_id)


def list_turns(session: Session, run_id: str) -> list[AgentTurn]:
    """List turns scoped to a run."""
    return list(
        session.scalars(
            select(AgentTurn)
            .where(AgentTurn.run_id == run_id)
            .order_by(AgentTurn.started_at, AgentTurn.turn_id)
        )
    )


def list_events(
    session: Session, run_id: str, after_event_id: int | None = None, limit: int = 100
) -> list[AgentTraceEvent]:
    """Page events by exclusive global event ID while constraining run."""
    query = select(AgentTraceEvent).where(AgentTraceEvent.run_id == run_id)
    if after_event_id is not None:
        query = query.where(AgentTraceEvent.event_id > after_event_id)
    return list(session.scalars(query.order_by(AgentTraceEvent.event_id).limit(limit)))


def job_exists(session: Session, job_id: str) -> bool:
    """Check a parent job exists."""
    return (
        session.scalar(select(ResearchJob.job_id).where(ResearchJob.job_id == job_id)) is not None
    )
