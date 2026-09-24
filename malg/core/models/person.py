"""Lean person contract for native Twenty Person records."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class PersonData(BaseModel):
    """Canonical person fields; company linkage is host-owned metadata."""

    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(max_length=200)
    last_name: str | None = Field(default=None, min_length=1, max_length=200)
    job_title: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    linkedin_url: HttpUrl | None = None
