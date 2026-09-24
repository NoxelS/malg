"""Operational memory routes retained after local CRM CRUD retirement."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Response, status
from nooa_memory.embeddings import HashingEmbedder  # type: ignore[import-untyped]
from nooa_memory.schema import Memory  # type: ignore[import-untyped]
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from malg.api.dependencies import SessionDependency
from malg.core.models.memory import MemoryPage
from malg.database.memory import PostgresMemoryStore
from malg.database.session import make_session_factory

router = APIRouter(prefix="/api/v1", tags=["memory"])


def _memory_store(session: Session) -> PostgresMemoryStore:
    """Build the shared memory store from the request's configured engine."""
    return PostgresMemoryStore(make_session_factory(cast(Engine, session.get_bind())))


@router.post("/memories", response_model=Memory, status_code=status.HTTP_201_CREATED)
def create_memory(payload: Memory, session: SessionDependency) -> Memory:
    """Create one administrative memory record and its outgoing edges."""
    store = _memory_store(session)
    if store.get(payload.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="memory already exists")
    try:
        return store.add(payload, HashingEmbedder().embed(payload.embedding_text()))
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="memory already exists"
        ) from error


@router.get("/memories", response_model=MemoryPage)
def list_memories(
    session: SessionDependency,
    owner: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> MemoryPage:
    """List memories with exact owner filtering and stable pagination."""
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(status_code=422, detail="invalid pagination")
    store = _memory_store(session)
    items = store.all_memories(include_archived=include_archived, owner=owner)
    if owner is not None:
        items = [item for item in items if item.owner == owner]
    return MemoryPage(
        items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset
    )


def _memory_or_404(store: PostgresMemoryStore, memory_id: str) -> Memory:
    """Return one memory or the stable administrative not-found response."""
    memory = store.get(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return memory


@router.get("/memories/{memory_id}", response_model=Memory)
def get_memory(memory_id: str, session: SessionDependency) -> Memory:
    """Return one durable memory record."""
    return _memory_or_404(_memory_store(session), memory_id)


@router.put("/memories/{memory_id}", response_model=Memory)
def replace_memory(memory_id: str, payload: Memory, session: SessionDependency) -> Memory:
    """Replace a memory, re-embedding only when content changes."""
    if payload.id != memory_id:
        raise HTTPException(status_code=422, detail="memory ID mismatch")
    store = _memory_store(session)
    existing = _memory_or_404(store, memory_id)
    embedding = (
        HashingEmbedder().embed(payload.embedding_text())
        if payload.content != existing.content
        else None
    )
    try:
        if embedding is None:
            store.save(payload)
        else:
            store.add(payload, embedding)
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="memory update conflict") from error
    return payload


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str, session: SessionDependency) -> Response:
    """Physically delete a memory and all inbound and outbound edges."""
    store = _memory_store(session)
    _memory_or_404(store, memory_id)
    store.delete(memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
