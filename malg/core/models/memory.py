"""API response models for durable agent memory."""

from __future__ import annotations

from nooa_memory.schema import Memory  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field


class MemoryPage(BaseModel):
    """A bounded page of canonical NOOA memory records."""

    model_config = ConfigDict(from_attributes=True)

    items: list[Memory]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class MemoryOverviewRecord(BaseModel):
    """Compact metadata used to browse memory without loading its full payload."""

    id: str
    type: str
    content_preview: str
    owner: str
    importance: float
    salience: float
    strength: int
    access_count: int
    created_at: float
    last_accessed_at: float
    status: str | None = None
    archived: bool


class MemoryOverviewPage(BaseModel):
    """A bounded, filtered page of compact memory overview records."""

    items: list[MemoryOverviewRecord]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class MemoryClearResult(BaseModel):
    """Count returned after globally deleting durable memory records."""

    deleted: int
