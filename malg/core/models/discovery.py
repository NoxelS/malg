"""Versioned bounded company-discovery contracts; models have no execution side effects."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl, StrictBool, computed_field, model_validator

from malg.core.models.account import AccountData, AccountIdentity
from malg.core.models.jobs import ResearchJobStatus
from malg.core.models.research import ResearchModel

PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeStrictInt = Annotated[int, Field(strict=True, ge=0)]


class DiscoveryBudgetLimits(ResearchModel):
    """Required finite host ceilings; validation rejects nonpositive or noninteger limits."""

    max_pages: PositiveStrictInt
    max_requests: PositiveStrictInt
    max_bytes: PositiveStrictInt
    max_seconds: PositiveStrictInt
    max_model_calls: PositiveStrictInt


class DiscoverySourcePolicy(ResearchModel):
    """Proposed seed and reuse policy; URLs are not authorization to fetch."""

    seed_urls: list[HttpUrl] = Field(default_factory=list)
    reuse_sources: StrictBool
    follow_observed_links: StrictBool


class DiscoveryScope(ResearchModel):
    """Versioned saved scope snapshot; persistence and compare-and-swap are external."""

    schema_version: Literal[1] = 1
    scope_id: UUID
    revision: Annotated[int, Field(strict=True, ge=1)]
    enabled: StrictBool
    source_policy: DiscoverySourcePolicy
    budgets: DiscoveryBudgetLimits


class DiscoveryBatchInput(ResearchModel):
    """Immutable admitted scope snapshot and time window; contains no claim ownership."""

    schema_version: Literal[1] = 1
    batch_id: UUID
    scope: DiscoveryScope
    started_at: AwareDatetime
    deadline_at: AwareDatetime

    @model_validator(mode="after")
    def validate_window(self) -> DiscoveryBatchInput:
        """Require enabled scope and a positive window within the configured time ceiling."""
        if not self.scope.enabled:
            raise ValueError("scope must be enabled")
        duration = (self.deadline_at - self.started_at).total_seconds()
        if duration <= 0 or duration > self.scope.budgets.max_seconds:
            raise ValueError("batch window must be positive and within max_seconds")
        return self


class DiscoveryCounts(ResearchModel):
    """Distinct durably recorded identities; known is pre-batch inventory, not replay count."""

    observed_companies: NonNegativeStrictInt = 0
    new_companies: NonNegativeStrictInt = 0
    known_companies: NonNegativeStrictInt = 0

    @model_validator(mode="after")
    def validate_total(self) -> DiscoveryCounts:
        """Keep the observed total equal to its new and pre-existing partitions."""
        if self.observed_companies != self.new_companies + self.known_companies:
            raise ValueError("observed_companies must equal new_companies plus known_companies")
        return self


class DiscoveryRetrievalSummary(ResearchModel):
    """Completed host observation-attempt counters with derived, non-overridable health."""

    usable: NonNegativeStrictInt = 0
    deferred: NonNegativeStrictInt = 0
    failed: NonNegativeStrictInt = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def health(self) -> Literal["not_attempted", "healthy", "degraded", "unavailable"]:
        """Classify whether observations were attempted and content was usable."""
        attempts = self.usable + self.deferred + self.failed
        if attempts == 0:
            return "not_attempted"
        if self.usable == 0:
            return "unavailable"
        return "healthy" if self.deferred == 0 and self.failed == 0 else "degraded"


class DiscoveryStopReason(StrEnum):
    """Why bounded discovery completed or stopped."""

    WORK_COMPLETED = "work_completed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_ELIGIBLE_WORK = "no_eligible_work"
    SOURCE_ACCESS_DEFERRED = "source_access_deferred"
    CANCELLED = "cancelled"
    OPERATIONAL_FAILURE = "operational_failure"


class DiscoveryFailureCode(StrEnum):
    """Safe public failure categories without upstream payload or exception details."""

    RETRIEVAL_FAILED = "retrieval_failed"
    MODEL_FAILED = "model_failed"
    INVALID_TOOL_CONTRACT = "invalid_tool_contract"
    STORAGE_FAILED = "storage_failed"
    INTERNAL_ERROR = "internal_error"


class DiscoveryBatchResult(ResearchModel):
    """Validated terminal/progress snapshot; constructing it does not prove durable storage."""

    schema_version: Literal[1] = 1
    batch_id: UUID
    status: ResearchJobStatus
    retrieval: DiscoveryRetrievalSummary = Field(default_factory=DiscoveryRetrievalSummary)
    counts: DiscoveryCounts = Field(default_factory=DiscoveryCounts)
    checkpoint_sequence: NonNegativeStrictInt
    stop_reason: DiscoveryStopReason | None = None
    failure_code: DiscoveryFailureCode | None = None
    exhausted_budget: Literal["pages", "requests", "bytes", "time", "model_calls"] | None = None
    next_eligible_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_state(self) -> DiscoveryBatchResult:
        """Enforce status/reason invariants and tie yield to committed usable progress."""
        if self.counts.observed_companies and (
            self.checkpoint_sequence == 0 or self.retrieval.usable == 0
        ):
            raise ValueError("company counts require a committed usable observation")
        if self.status == ResearchJobStatus.QUEUED:
            if (
                self.checkpoint_sequence
                or self.counts.observed_companies
                or any((self.retrieval.usable, self.retrieval.deferred, self.retrieval.failed))
                or any(
                    (
                        self.stop_reason,
                        self.failure_code,
                        self.exhausted_budget,
                        self.next_eligible_at,
                    )
                )
            ):
                raise ValueError("queued result must be empty and have no terminal fields")
        elif self.status == ResearchJobStatus.RUNNING:
            if any(
                (self.stop_reason, self.failure_code, self.exhausted_budget, self.next_eligible_at)
            ):
                raise ValueError("running result cannot have terminal fields")
        elif self.status == ResearchJobStatus.SUCCEEDED:
            if (
                self.stop_reason
                not in {
                    DiscoveryStopReason.WORK_COMPLETED,
                    DiscoveryStopReason.BUDGET_EXHAUSTED,
                    DiscoveryStopReason.NO_ELIGIBLE_WORK,
                    DiscoveryStopReason.SOURCE_ACCESS_DEFERRED,
                }
                or self.failure_code is not None
            ):
                raise ValueError("succeeded result requires a nonfailure stop reason")
            if (self.stop_reason == DiscoveryStopReason.BUDGET_EXHAUSTED) != (
                self.exhausted_budget is not None
            ):
                raise ValueError("exhausted_budget is required only for budget completion")
            if self.stop_reason == DiscoveryStopReason.NO_ELIGIBLE_WORK and (
                self.retrieval.health != "not_attempted"
                or self.counts.observed_companies
                or self.checkpoint_sequence
            ):
                raise ValueError("no_eligible_work requires no attempts or progress")
            if (
                self.stop_reason == DiscoveryStopReason.SOURCE_ACCESS_DEFERRED
                and self.retrieval.deferred == 0
            ):
                raise ValueError("source_access_deferred requires a deferred observation")
            if (
                self.stop_reason == DiscoveryStopReason.WORK_COMPLETED
                and self.retrieval.usable == 0
            ):
                raise ValueError("work_completed requires usable content")
            if self.next_eligible_at is not None and self.stop_reason not in {
                DiscoveryStopReason.NO_ELIGIBLE_WORK,
                DiscoveryStopReason.SOURCE_ACCESS_DEFERRED,
            }:
                raise ValueError("next_eligible_at is allowed only for deferred/no-work completion")
        elif self.status == ResearchJobStatus.FAILED:
            if (
                self.stop_reason != DiscoveryStopReason.OPERATIONAL_FAILURE
                or self.failure_code is None
                or self.exhausted_budget is not None
                or self.next_eligible_at is not None
            ):
                raise ValueError("failed result requires operational_failure and a failure code")
        elif self.status == ResearchJobStatus.CANCELLED and (
            self.stop_reason != DiscoveryStopReason.CANCELLED
            or self.failure_code is not None
            or self.exhausted_budget is not None
            or self.next_eligible_at is not None
        ):
            raise ValueError("cancelled result requires cancelled and no failure code")
        return self


def validate_batch_transition(
    previous: DiscoveryBatchResult, current: DiscoveryBatchResult
) -> None:
    """Validate one pure lifecycle transition; this is not a durable ownership check."""
    if previous.batch_id != current.batch_id or previous.schema_version != current.schema_version:
        raise ValueError("batch identity and schema version must remain fixed")
    if previous == current:
        return
    if previous.status in {
        ResearchJobStatus.SUCCEEDED,
        ResearchJobStatus.FAILED,
        ResearchJobStatus.CANCELLED,
    }:
        raise ValueError("terminal snapshots are immutable")
    allowed = {
        (ResearchJobStatus.QUEUED, ResearchJobStatus.RUNNING),
        (ResearchJobStatus.QUEUED, ResearchJobStatus.CANCELLED),
        (ResearchJobStatus.RUNNING, ResearchJobStatus.RUNNING),
        (ResearchJobStatus.RUNNING, ResearchJobStatus.SUCCEEDED),
        (ResearchJobStatus.RUNNING, ResearchJobStatus.FAILED),
        (ResearchJobStatus.RUNNING, ResearchJobStatus.CANCELLED),
    }
    if (previous.status, current.status) not in allowed:
        raise ValueError("invalid batch lifecycle transition")
    if (
        current.checkpoint_sequence < previous.checkpoint_sequence
        or current.counts.observed_companies < previous.counts.observed_companies
    ):
        raise ValueError("progress cannot regress")
    if any(
        getattr(current.retrieval, key) < getattr(previous.retrieval, key)
        for key in ("usable", "deferred", "failed")
    ):
        raise ValueError("retrieval counters cannot regress")
    if (
        current.counts != previous.counts
        and current.checkpoint_sequence <= previous.checkpoint_sequence
    ):
        raise ValueError("company counts may change only with a new checkpoint")
    if (
        previous.status == ResearchJobStatus.QUEUED
        and current.status == ResearchJobStatus.CANCELLED
        and (
            current.checkpoint_sequence
            or current.counts.observed_companies
            or current.retrieval.usable
            or current.retrieval.deferred
            or current.retrieval.failed
        )
    ):
        raise ValueError("queued cancellation cannot contain work")


class DiscoveryCompanyState(ResearchModel):
    """Versioned inventory observation, independent of identity resolution and qualification."""

    schema_version: Literal[1] = 1
    company_id: UUID
    data: AccountData
    identity: AccountIdentity | None = None
    identity_state: Literal["provisional", "resolved", "conflicted"]
    evidence_version: NonNegativeStrictInt
    source_refs: list[str] = Field(min_length=1)
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_observation(self) -> DiscoveryCompanyState:
        """Require provenance, ordered timestamps, and identity for resolved records."""
        if (
            any(not ref.strip() for ref in self.source_refs)
            or self.last_observed_at < self.first_observed_at
        ):
            raise ValueError("source references must be nonempty and observation times ordered")
        if self.identity_state == "resolved" and self.identity is None:
            raise ValueError("resolved identity state requires identity")
        return self


class CompanyICPAssessment(ResearchModel):
    """Historical evidence/ICP-version assessment; absence means unassessed, never mismatch."""

    schema_version: Literal[1] = 1
    company_id: UUID
    evidence_version: NonNegativeStrictInt
    icp_id: UUID
    icp_version: str = Field(min_length=1)
    evaluator_version: str = Field(min_length=1)
    outcome: Literal["match", "mismatch", "insufficient_evidence"]
    claim_refs: list[str] = Field(default_factory=list)
    unknowns: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=10
    )

    @model_validator(mode="after")
    def validate_evidence(self) -> CompanyICPAssessment:
        """Require decisive evidence for match/mismatch and unknowns for insufficient evidence."""
        if any(not ref.strip() for ref in self.claim_refs):
            raise ValueError("claim references must be nonempty")
        if self.outcome in {"match", "mismatch"} and (
            not self.claim_refs or self.evidence_version < 1
        ):
            raise ValueError("decisive assessment requires claim refs and verified evidence")
        if self.outcome == "insufficient_evidence" and not self.unknowns:
            raise ValueError("insufficient_evidence requires at least one unknown")
        return self


class CompanyPublicationState(ResearchModel):
    """Independent publication status; this record neither authorizes nor performs publication."""

    schema_version: Literal[1] = 1
    company_id: UUID
    status: Literal["not_published", "pending", "published", "failed"]
    remote_company_id: str | None = None

    @model_validator(mode="after")
    def validate_remote_id(self) -> CompanyPublicationState:
        """Require a remote identity when published while retaining reconciliation IDs otherwise."""
        if self.status == "published" and (
            self.remote_company_id is None or not self.remote_company_id.strip()
        ):
            raise ValueError("published state requires a remote company ID")
        return self
