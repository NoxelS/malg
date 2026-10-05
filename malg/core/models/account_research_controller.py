"""Public contracts for continuous account-research queue maintenance."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AccountResearchControllerConfiguration(BaseModel):
    """One validated account-research request that the controller may repeat."""

    model_config = ConfigDict(extra="forbid")
    campaign_id: UUID
    icp_id: UUID
    company_count: int = Field(strict=True, ge=1, le=20)
    people_per_company: int = Field(strict=True, ge=1, le=5)
    opportunities_per_company: int = Field(strict=True, ge=1, le=3)

    def job_payload(self) -> dict[str, object]:
        """Return the canonical immutable payload used for job matching."""
        return {"kind": "account", **self.model_dump(mode="json")}


class AccountResearchControllerState(BaseModel):
    """Desired persisted enabled state with optimistic-concurrency revision."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool
    revision: int = Field(ge=0)


class AccountResearchControllerRecord(BaseModel):
    """Safe controller configuration and current queue-maintenance status."""

    enabled: bool
    revision: int
    configuration: AccountResearchControllerConfiguration | None
    queued_matching_jobs: int
    running_matching_jobs: int
    last_checked_at: datetime | None
    last_enqueued_job_id: str | None
    last_error: str | None
    next_retry_at: datetime | None
