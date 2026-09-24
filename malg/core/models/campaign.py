"""Lean campaign generation contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CampaignData(BaseModel):
    """Canonical campaign fields owned by Twenty."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1000)
