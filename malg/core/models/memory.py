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
