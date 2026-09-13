"""Observable authenticated trace API behavior."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from tests.fixtures import authenticated_client

from malg.database.models import AgentRun, AgentTurn, Base, ResearchJob


def _engine() -> Engine:
    """Create an isolated SQLite engine with foreign keys enabled."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: sqlite3.Connection, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    return engine


def test_turns_endpoint_returns_legacy_scalar_response() -> None:
    """A scalar response persisted before response normalization remains readable."""
    engine = _engine()
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            ResearchJob.__table__.insert(),
            {"job_id": "job", "kind": "campaign", "status": "succeeded", "created_at": now},
        )
        connection.execute(
            AgentRun.__table__.insert(),
            {
                "run_id": "run",
                "job_id": "job",
                "scope": "agent",
                "agent_name": "CampaignResearchAgent",
                "method_name": "research",
                "status": "succeeded",
                "started_at": now,
            },
        )
        connection.execute(
            AgentTurn.__table__.insert(),
            {
                "run_id": "run",
                "generation_id": "generation",
                "turn_number": 1,
                "method_name": "research",
                "strategy": "default",
                "started_at": now,
                "success": True,
                "request_messages": [{"role": "user", "content": "Research."}],
                "request_params": {"temperature": 0},
                "response": "LLMResponse(raw_response=...)",
            },
        )

    client: TestClient = authenticated_client(engine)
    response = client.get("/api/v1/agent-runs/run/turns")

    assert response.status_code == 200
    assert response.json()[0]["response"] == "LLMResponse(raw_response=...)"
