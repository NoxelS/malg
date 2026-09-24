"""Validated contracts for durable research jobs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ResearchJobKind(StrEnum):
    """Supported bounded research stages."""

    CAMPAIGN = "campaign"
    ICP = "icp"
    DISCOVERY = "discovery"
    ACCOUNT = "account"
    ACCOUNT_HYDRATION = "account_hydration"
    PERSON = "person"
    PERSON_HYDRATION = "person_hydration"


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


class CampaignResearchJobBatchRequest(BaseModel):
    """Request a bounded batch of independently researched campaigns."""

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(strict=True, ge=1, le=100)


class ICPResearchJobRequest(BaseModel):
    """Request one ICP beneath an existing campaign."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.ICP] = ResearchJobKind.ICP
    campaign_id: UUID


class AccountResearchJobRequest(BaseModel):
    """Request one account beneath an existing campaign and ICP."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.ACCOUNT] = ResearchJobKind.ACCOUNT
    campaign_id: UUID | None = None
    icp_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=200)
    website: str | None = Field(default=None, min_length=1, max_length=2048)


class ICPResearchJobBatchRequest(BaseModel):
    """Request a bounded batch of ICP research jobs beneath a campaign."""

    model_config = ConfigDict(extra="forbid")
    campaign_id: UUID
    amount: int = Field(strict=True, ge=1, le=100)


class AccountResearchJobBatchRequest(BaseModel):
    """Request a bounded batch of account research jobs beneath an ICP."""

    model_config = ConfigDict(extra="forbid")
    campaign_id: UUID | None = None
    icp_id: UUID
    amount: int = Field(strict=True, ge=1, le=100)


class DiscoveryResearchJobRequest(BaseModel):
    """Request bounded candidate discovery from a persisted ICP."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.DISCOVERY] = ResearchJobKind.DISCOVERY
    campaign_id: UUID | None = None
    icp_id: UUID
    limit: int = Field(default=10, strict=True, ge=1, le=20)


class AccountHydrationJobRequest(BaseModel):
    """Hydrate only missing fields on an existing Twenty company."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.ACCOUNT_HYDRATION] = ResearchJobKind.ACCOUNT_HYDRATION
    account_id: UUID


class PersonResearchJobRequest(BaseModel):
    """Research one buyer-relevant person for an existing account and ICP."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.PERSON] = ResearchJobKind.PERSON
    account_id: UUID
    icp_id: UUID


class PersonHydrationJobRequest(BaseModel):
    """Hydrate only missing fields on an existing Twenty person."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.PERSON_HYDRATION] = ResearchJobKind.PERSON_HYDRATION
    person_id: UUID


ResearchJobRequest = Annotated[
    CampaignResearchJobRequest
    | ICPResearchJobRequest
    | AccountResearchJobRequest
    | DiscoveryResearchJobRequest
    | AccountHydrationJobRequest
    | PersonResearchJobRequest
    | PersonHydrationJobRequest,
    Field(discriminator="kind"),
]


class ResearchJobRecord(BaseModel):
    """API representation of a durable job and remote result references."""

    job_id: str
    kind: str
    status: ResearchJobStatus
    campaign_id: str | None = None
    icp_id: str | None = None
    account_id: str | None = None
    person_id: str | None = None
    request_payload: dict[str, object] | None = None
    contract_version: int | None = None
    contract_hash: str | None = None
    result_refs: list[dict[str, str | None]] = Field(default_factory=list)
    data_origin: str = "twenty"
    attempt_count: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_detail: str | None = None
    workflow_id: str | None = None
    stage_key: str | None = None
    deadline_at: datetime | None = None
    result_outcome: str | None = None
