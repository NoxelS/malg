"""Validated contracts for durable research jobs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchJobKind(StrEnum):
    """Supported one-artifact research units."""

    CAMPAIGN = "campaign"
    ICP = "icp"
    ACCOUNT = "account"


class ResearchJobStatus(StrEnum):
    """Durable lifecycle states for one research job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CampaignResearchJobRequest(BaseModel):
    """Request one independently researched campaign."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.CAMPAIGN] = ResearchJobKind.CAMPAIGN


class ICPResearchJobRequest(BaseModel):
    """Request one ICP beneath an existing campaign."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.ICP] = ResearchJobKind.ICP
    campaign_id: str = Field(min_length=1, max_length=80)


class AccountResearchJobRequest(BaseModel):
    """Request one account beneath an existing campaign and ICP."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.ACCOUNT] = ResearchJobKind.ACCOUNT
    campaign_id: str = Field(min_length=1, max_length=80)
    icp_id: str = Field(min_length=1, max_length=80)


ResearchJobRequest = Annotated[
    CampaignResearchJobRequest | ICPResearchJobRequest | AccountResearchJobRequest,
    Field(discriminator="kind"),
]


class ResearchJobRecord(BaseModel):
    """API representation of a job and only its persisted artifact references."""

    job_id: str
    kind: ResearchJobKind
    status: ResearchJobStatus
    campaign_id: str | None = None
    icp_id: str | None = None
    account_match_id: str | None = None
    attempt_count: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_detail: str | None = None
