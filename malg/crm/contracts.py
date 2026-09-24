"""Typed envelopes and outcomes for the Twenty integration boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from malg.core.models.account import AccountData
from malg.core.models.campaign import CampaignData
from malg.core.models.icp import ICPData
from malg.core.models.person import PersonData


class CRMRecord[T: CampaignData | ICPData | AccountData | PersonData](BaseModel):
    """A remote record plus host-only observations not represented in business data."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    data: T
    updated_at: datetime
    campaign_id: UUID | None = None
    company_id: UUID | None = None
    observed_composites: dict[str, dict[str, Any]] = Field(default_factory=dict)
    official_domain: str | None = None

    def targeting_snapshot(self) -> dict[str, Any]:
        """Capture relevant fields and real parent references, excluding timestamps."""
        return {
            "id": str(self.id),
            "data": self.data.model_dump(mode="json"),
            "campaign_id": str(self.campaign_id) if self.campaign_id else None,
            "company_id": str(self.company_id) if self.company_id else None,
        }


class CRMPage[T: CampaignData | ICPData | AccountData | PersonData](BaseModel):
    """Bounded, cursor-based page of typed remote records."""

    model_config = ConfigDict(extra="forbid")

    items: list[CRMRecord[T]]
    next_cursor: str | None = None


class MembershipRecord(BaseModel):
    """Company-to-ICP membership stored as a managed Twenty object."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    company_id: UUID
    icp_id: UUID
    updated_at: datetime


class MutationOutcome(BaseModel):
    """Sanitized result of a remote write operation."""

    model_config = ConfigDict(extra="forbid")

    record_id: UUID
    changed_fields: tuple[str, ...] = Field(default_factory=tuple)
    conflict: bool = False
    detail: str | None = None
