"""PostgreSQL-backed durable memory integration contract."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from nooa_memory.schema import Edge, EdgeType, Memory
from sqlalchemy import text

from malg.api.app import create_app
from malg.database.memory import PostgresMemoryStore
from malg.database.session import make_engine, make_session_factory


def test_postgres_memory_store_and_api_crud() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for the PostgreSQL integration test")
    engine = make_engine(database_url)
    sessions = make_session_factory(engine)
    store = PostgresMemoryStore(sessions)
    store.delete("ci-memory")
    store.add_edge("ci-memory-source", "ci-memory")

    client = TestClient(create_app(database_engine=engine))
    memory = Memory(
        id="ci-memory",
        content="PostgreSQL keeps durable agent findings.",
        owner="ci",
        edges=[Edge(target_id="ci-memory-source", type=EdgeType.RELATED)],
    )
    created = client.post("/api/v1/memories", json=memory.model_dump(mode="json"))
    assert created.status_code == 201
    assert created.json()["owner"] == "ci"
    assert client.post("/api/v1/memories", json=memory.model_dump(mode="json")).status_code == 409

    listed = client.get("/api/v1/memories", params={"owner": "ci"})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == "ci-memory"

    replacement = memory.model_copy(update={"content": "PostgreSQL persists updated findings."})
    updated = client.put("/api/v1/memories/ci-memory", json=replacement.model_dump(mode="json"))
    assert updated.status_code == 200
    assert updated.json()["content"] == replacement.content

    assert client.delete("/api/v1/memories/ci-memory").status_code == 204
    with sessions() as session:
        assert session.scalar(text("SELECT 1 FROM agent_memories WHERE id = 'ci-memory'")) is None
        assert (
            session.scalar(text("SELECT 1 FROM agent_memory_edges WHERE target_id = 'ci-memory'"))
            is None
        )
    engine.dispose()
