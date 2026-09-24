"""SQLite-backed behavioral coverage for the operational memory store."""

from __future__ import annotations

import sqlite3
from typing import Any

from nooa_memory.schema import Edge, EdgeType, Memory
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from malg.database.memory import PostgresMemoryStore
from malg.database.models import Base
from malg.database.session import make_session_factory


def _engine() -> Engine:
    """Exercise portable CRUD, emulating only PostgreSQL catalog reads.

    The SQLite JSON vector variant does not prove KNN or extension availability;
    those require the separate real-PostgreSQL integration scenario.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: sqlite3.Connection, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def emulate_postgres_catalog(
        _connection: Any,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> tuple[str, Any]:
        if statement == "SELECT to_regclass('agent_memories')":
            return (
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'agent_memories'",
                (),
            )
        if statement == "SELECT 1 FROM pg_extension WHERE extname = 'vector'":
            return "SELECT 1", ()
        return statement, parameters

    Base.metadata.create_all(engine)
    return engine


def _store(engine: Engine) -> PostgresMemoryStore:
    return PostgresMemoryStore(make_session_factory(engine))


def _memory(memory_id: str, content: str, *, owner: str = "", archived: bool = False) -> Memory:
    return Memory(
        id=memory_id,
        content=content,
        owner=owner,
        archived=archived,
        edges=[],
    )


def test_store_round_trip_replaces_edges_and_preserves_changed_content() -> None:
    engine = _engine()
    store = _store(engine)
    source = _memory(
        "source",
        "original finding",
        owner="worker@one",
    ).model_copy(
        update={
            "edges": [
                Edge(target_id="target", type=EdgeType.RELATED),
                Edge(target_id="dangling", type=EdgeType.CAUSES),
            ]
        }
    )
    store.add(source)
    assert {edge.target_id for edge in store.neighbors("source")} == {"target", "dangling"}

    replacement = source.model_copy(
        update={
            "content": "changed finding",
            "edges": [Edge(target_id="target", type=EdgeType.RELATED)],
        }
    )
    store.save(replacement)
    loaded = store.get("source")
    assert loaded is not None
    assert loaded.content == "changed finding"
    assert [edge.target_id for edge in loaded.edges] == ["target"]

    engine.dispose()


def test_store_owner_visibility_archive_and_delete_remove_orphan_edges() -> None:
    engine = _engine()
    store = _store(engine)
    store.add(_memory("shared", "shared", owner=""))
    store.add(_memory("role", "role", owner="researcher"))
    store.add(_memory("private", "private", owner="researcher@alice"))
    store.add(_memory("archived", "old", owner="researcher", archived=True))
    store.add_edge("private", "shared")
    store.add_edge("shared", "private")

    assert {item.id for item in store.all_memories(owner="researcher")} == {
        "shared",
        "role",
        "private",
    }
    assert {item.id for item in store.all_memories(owner="researcher@alice")} == {
        "shared",
        "private",
    }
    assert {item.id for item in store.all_memories(include_archived=True)} == {
        "shared",
        "role",
        "private",
        "archived",
    }
    assert store.count(owner="researcher") == 3

    store.delete("private")
    assert store.count(owner="researcher") == 2
    assert store.neighbors("shared") == []
    with Session(engine) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM agent_memory_edges WHERE target_id = 'private'")
            ).scalar_one()
            == 0
        )
    engine.dispose()
