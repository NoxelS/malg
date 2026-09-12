"""PostgreSQL implementation of NOOA's memory-store contract."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from typing import Any, cast

import numpy as np
from nooa_memory.schema import Edge, EdgeType, Memory, MemoryType  # type: ignore[import-untyped]
from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from malg.database.models import AgentMemory, AgentMemoryEdge, AgentMemoryMaintenance

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class PostgresMemoryStore:
    """Short-session PostgreSQL store preserving NOOA memory semantics.

    The store never owns a connection or filesystem path. Every operation uses a
    short-lived session from the supplied factory, making it safe for API and
    worker processes sharing one database.
    """

    path = "postgresql:agent_memories"

    def __init__(self, sessions: sessionmaker[Session], *, embedding_dim: int = 256) -> None:
        if embedding_dim != 256:
            raise ValueError("PostgreSQL NOOA memory requires a 256-dimensional embedder")
        self.sessions = sessions
        self.embedding_dim = embedding_dim
        try:
            with self.sessions() as session:
                schema = session.scalar(text("SELECT to_regclass('agent_memories')"))
                vector = session.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        except Exception as error:
            raise RuntimeError(
                "PostgreSQL memory schema is unavailable; run Alembic migrations first."
            ) from error
        if schema is None or vector is None:
            raise RuntimeError(
                "PostgreSQL memory schema or vector extension is missing; run Alembic migrations first."
            )

    @staticmethod
    def _payload(memory: Memory) -> dict[str, Any]:
        return cast(dict[str, Any], memory.model_dump(mode="json", exclude={"edges"}))

    @staticmethod
    def _embedding(value: np.ndarray | list[float] | None) -> list[float] | None:
        if value is None:
            return None
        array = np.asarray(value, dtype=np.float32).reshape(-1)
        if array.size != 256:
            raise ValueError("memory embedding must have 256 dimensions")
        return array.tolist()

    def _edges(self, session: Session, source_id: str) -> list[Edge]:
        rows = session.scalars(
            select(AgentMemoryEdge).where(AgentMemoryEdge.source_id == source_id)
        ).all()
        return [
            Edge(
                target_id=row.target_id,
                type=EdgeType(row.type),
                weight=row.weight,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def _memory(self, session: Session, row: AgentMemory) -> Memory:
        payload = dict(row.payload)
        payload.update({"archived": row.archived, "owner": row.owner, "status": row.status})
        payload["edges"] = [edge.model_dump(mode="json") for edge in self._edges(session, row.id)]
        return Memory.model_validate(payload)

    @staticmethod
    def _owner_clauses(owner: str | None) -> list[Any]:
        """Apply NOOA's shared, role, and role-instance visibility rules."""
        if owner is None:
            return []
        if "@" in owner:
            return [AgentMemory.owner.in_([owner, ""])]
        return [
            (AgentMemory.owner == "")
            | (AgentMemory.owner == owner)
            | AgentMemory.owner.like(f"{owner}@%"),
        ]

    def add(self, memory: Memory, embedding: np.ndarray | None = None) -> Memory:
        with self.sessions() as session:
            row = session.get(AgentMemory, memory.id)
            if row is None:
                row = AgentMemory(id=memory.id)
                session.add(row)
            row.type = memory.type.value
            row.content = memory.content
            row.importance = memory.importance
            row.salience = memory.salience
            row.strength = memory.strength
            row.created_at = memory.created_at
            row.last_accessed_at = memory.last_accessed_at
            row.access_count = memory.access_count
            row.archived = memory.archived
            row.owner = memory.owner
            row.status = memory.status
            row.payload = self._payload(memory)
            if embedding is not None:
                row.embedding = self._embedding(embedding)
            session.execute(delete(AgentMemoryEdge).where(AgentMemoryEdge.source_id == memory.id))
            for edge in memory.edges:
                session.add(
                    AgentMemoryEdge(
                        source_id=memory.id,
                        target_id=edge.target_id,
                        type=edge.type.value,
                        weight=edge.weight,
                        created_at=edge.created_at,
                    )
                )
            session.commit()
        return memory

    def save(self, memory: Memory) -> None:
        self.add(memory)

    def add_edge(
        self, src: str, dst: str, type: EdgeType = EdgeType.RELATED, weight: float = 1.0
    ) -> None:
        with self.sessions() as session:
            row = session.get(AgentMemoryEdge, (src, dst, type.value))
            if row is None:
                row = AgentMemoryEdge(source_id=src, target_id=dst, type=type.value)
                session.add(row)
            row.weight = weight
            row.created_at = time.time()
            session.commit()

    def archive(self, id: str) -> None:
        with self.sessions() as session:
            session.execute(update(AgentMemory).where(AgentMemory.id == id).values(archived=True))
            session.commit()

    def delete(self, id: str) -> None:
        with self.sessions() as session:
            session.execute(
                delete(AgentMemoryEdge).where(
                    (AgentMemoryEdge.source_id == id) | (AgentMemoryEdge.target_id == id)
                )
            )
            session.execute(delete(AgentMemory).where(AgentMemory.id == id))
            session.commit()

    def get(self, id: str) -> Memory | None:
        with self.sessions() as session:
            row = session.get(AgentMemory, id)
            return self._memory(session, row) if row else None

    def resolve_id(self, id_or_prefix: str) -> str | None:
        with self.sessions() as session:
            exact = session.get(AgentMemory, id_or_prefix)
            if exact:
                return exact.id
            if len(id_or_prefix) < 6:
                return None
            ids = session.scalars(
                select(AgentMemory.id).where(AgentMemory.id.like(f"{id_or_prefix}%")).limit(2)
            ).all()
            if len(ids) > 1:
                raise ValueError(f"memory id prefix {id_or_prefix!r} is ambiguous")
            return ids[0] if ids else None

    def owner_of(self, id: str) -> str | None:
        with self.sessions() as session:
            return session.scalar(select(AgentMemory.owner).where(AgentMemory.id == id))

    def get_embedding(self, id: str) -> np.ndarray | None:
        with self.sessions() as session:
            value = session.scalar(select(AgentMemory.embedding).where(AgentMemory.id == id))
            return np.asarray(value, dtype=np.float32) if value is not None else None

    def neighbors(self, id: str) -> list[Edge]:
        with self.sessions() as session:
            return self._edges(session, id)

    def _where(self, *, include_archived: bool, owner: str | None) -> list[Any]:
        clauses: list[Any] = []
        if not include_archived:
            clauses.append(AgentMemory.archived.is_(False))
        if owner is not None:
            clauses.extend(self._owner_clauses(owner))
        return clauses

    def all_memories(
        self, *, include_archived: bool = False, owner: str | None = None
    ) -> list[Memory]:
        with self.sessions() as session:
            rows = session.scalars(
                select(AgentMemory)
                .where(*self._where(include_archived=include_archived, owner=owner))
                .order_by(AgentMemory.created_at.desc(), AgentMemory.id.desc())
            ).all()
            return [self._memory(session, row) for row in rows]

    def iter_memories(
        self, *, include_archived: bool = False, owner: str | None = None
    ) -> Iterator[Memory]:
        yield from self.all_memories(include_archived=include_archived, owner=owner)

    def count(self, *, include_archived: bool = False, owner: str | None = None) -> int:
        with self.sessions() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(AgentMemory)
                    .where(*self._where(include_archived=include_archived, owner=owner))
                )
                or 0
            )

    def knn(
        self, query_vec: np.ndarray, k: int, *, owner: str | None = None
    ) -> list[tuple[str, float]]:
        vector = self._embedding(query_vec)
        if owner is None:
            owner_clause = ""
        elif "@" in owner:
            owner_clause = " AND (owner = :owner OR owner = '')"
        else:
            owner_clause = " AND (owner = :owner OR owner LIKE :owner_prefix OR owner = '')"
        statement = text(
            f"SELECT id, embedding <=> :embedding AS distance FROM agent_memories "
            f"WHERE archived = false AND embedding IS NOT NULL{owner_clause} "
            "ORDER BY embedding <=> :embedding LIMIT :limit"
        ).bindparams(bindparam("embedding", type_=Vector(256)))
        params = {"embedding": vector, "owner": owner, "owner_prefix": f"{owner}@%", "limit": k}
        with self.sessions() as session:
            rows = session.execute(statement, params).all()
            return [(str(row.id), 1.0 - float(row.distance)) for row in rows]

    def keyword_search(
        self,
        text_value: str,
        k: int,
        *,
        mem_type: MemoryType | None = None,
        owner: str | None = None,
    ) -> list[str]:
        tokens = list(dict.fromkeys(_TOKEN_RE.findall(text_value.casefold())))[:12]
        if not tokens:
            return []
        with self.sessions() as session:
            clauses = self._where(include_archived=False, owner=owner)
            if mem_type is not None:
                clauses.append(AgentMemory.type == mem_type.value)
            rows = session.scalars(select(AgentMemory).where(*clauses)).all()
            ranked = sorted(
                (
                    (sum(token in row.content.casefold() for token in tokens), row.id)
                    for row in rows
                ),
                reverse=True,
            )
            return [mid for score, mid in ranked if score > 0][:k]

    def log_maintenance(self, kind: str, report: dict) -> None:
        with self.sessions() as session:
            session.add(AgentMemoryMaintenance(ts=time.time(), kind=kind, report=report))
            session.commit()

    def maintenance_history(self, limit: int = 20) -> list[dict]:
        with self.sessions() as session:
            rows = session.scalars(
                select(AgentMemoryMaintenance)
                .order_by(AgentMemoryMaintenance.id.desc())
                .limit(limit)
            ).all()
            return [{"ts": row.ts, "kind": row.kind, "report": row.report} for row in rows]

    def refresh_if_changed(self) -> bool:
        return False

    def close(self) -> None:
        return None
