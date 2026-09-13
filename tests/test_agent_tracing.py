from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

from nooa.runtime.middleware import LLMCallContext
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from malg.core.agent_tracing import AgentTraceRecorder, normalize
from malg.database.models import AgentTraceEvent, Base, ResearchJob


def test_trace_recorder_persists_order_and_repr_fallback() -> None:
    """Trace events retain order and represent values JSON cannot encode."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions.begin() as session:
        session.add(ResearchJob(job_id="job", kind="campaign", status="queued"))
    recorder = AgentTraceRecorder.start_run(sessions, "job", "worker", "ResearchWorker", "run_once")
    recorder.event("first", {"value": object()})
    recorder.event("second", {"raw": [1, "two"]})
    recorder.finish_success()
    with sessions() as session:
        events = list(session.scalars(select(AgentTraceEvent).order_by(AgentTraceEvent.sequence)))
        assert [event.event_type for event in events] == ["first", "second"]
        assert events[0].payload["value"].startswith("<object object at")
        assert events[1].payload == {"raw": [1, "two"]}
    assert normalize({"nested": (1, 2)}) == {"nested": [1, 2]}


@dataclass
class _LLMResponse:
    """Minimal NOOA-compatible response fixture."""

    content: str
    usage: dict[str, int]


def test_normalize_preserves_dataclass_fields_as_json() -> None:
    """LLM response dataclasses remain structured trace payloads."""
    assert normalize(_LLMResponse("Done.", {"total_tokens": 12})) == {
        "content": "Done.",
        "usage": {"total_tokens": 12},
    }


class _EventManager:
    """Capture a middleware registration for isolated execution."""

    def on(self, _event_name: str, _callback: object) -> object:
        return lambda: None

    def intercept(self, _event_name: str, middleware: object) -> object:
        self.middleware = middleware
        return lambda: None


class _Agent:
    """Minimal NOOA-compatible event publisher."""

    def __init__(self) -> None:
        self.event_manager = _EventManager()


class _FailingSessions:
    """Fail every trace persistence transaction."""

    @contextmanager
    def begin(self) -> object:
        raise RuntimeError("trace database unavailable")
        yield


def test_turn_trace_persistence_failure_does_not_fail_llm_call() -> None:
    """The LLM call succeeds when durable turn recording cannot start."""
    recorder = AgentTraceRecorder(_FailingSessions(), "run")
    agent = _Agent()
    recorder.attach(agent)

    async def next_call(_context: LLMCallContext) -> SimpleNamespace:
        return SimpleNamespace(response={"content": "done"})

    result = asyncio.run(agent.event_manager.middleware(LLMCallContext(messages=[]), next_call))

    assert result.response == {"content": "done"}


def test_trace_reset_callback_and_close_are_safe_together() -> None:
    """A caller-managed reset does not leave close with a consumed token."""
    recorder = AgentTraceRecorder(object(), "run")
    reset = recorder.bind()

    reset()
    recorder.close()
