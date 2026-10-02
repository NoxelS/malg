"""Validated contracts for durable research jobs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ResearchJobKind(StrEnum):
    """User-visible autonomous research jobs."""

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
    """Request distinct ICPs beneath an existing campaign."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.ICP] = ResearchJobKind.ICP
    campaign_id: UUID
    icp_count: int = Field(default=1, strict=True, ge=1, le=20)


class AccountResearchJobRequest(BaseModel):
    """Request complete Company, Person and Opportunity bundles for one ICP."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[ResearchJobKind.ACCOUNT] = ResearchJobKind.ACCOUNT
    icp_id: UUID
    company_count: int = Field(default=1, strict=True, ge=1, le=20)
    people_per_company: int = Field(default=1, strict=True, ge=1, le=5)
    opportunities_per_company: int = Field(default=1, strict=True, ge=1, le=3)


ResearchJobRequest = Annotated[
    CampaignResearchJobRequest | ICPResearchJobRequest | AccountResearchJobRequest,
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


class ResearchJobOverviewRecord(BaseModel):
    """Compact read model for browsing durable research jobs.

    The overview deliberately omits request payloads, result references, and
    diagnostics. Clients fetch those potentially large fields from the job
    detail endpoint after an operator chooses a row.
    """

    job_id: str
    kind: str
    status: ResearchJobStatus
    result_outcome: str | None = None
    attempt_count: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deadline_at: datetime | None = None
    campaign_id: str | None = None
    icp_id: str | None = None
    workflow_id: str | None = None
    stage_key: str | None = None


class ResearchJobOverviewPage(BaseModel):
    """One bounded page of filtered research-job overview records."""

    items: list[ResearchJobOverviewRecord]
    total: int
    limit: int
    offset: int
