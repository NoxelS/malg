from __future__ import annotations

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
