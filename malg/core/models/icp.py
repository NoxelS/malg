"""Structured output contract for ideal customer profile research."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from malg.core.models.evidence import EvidenceItem


class BuyingRoleType(StrEnum):
    ECONOMIC_BUYER = "economic_buyer"
    CHAMPION = "champion"
    USER = "user"
    TECHNICAL_APPROVER = "technical_approver"
    SECURITY_LEGAL_PROCUREMENT = "security_legal_procurement"


class Firmographics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industries: list[str] = Field(min_length=1)
    nace_codes: list[str] = Field(min_length=1)
    countries: list[str] = Field(min_length=1)
    employee_range: str
    turnover_range: str | None = None
    ownership_and_stage: list[str]
    operating_footprint: list[str]
    regulated_data_exposure: list[str]


class OperatingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_workflows: list[str] = Field(min_length=1)
    work_volume_signals: list[str]
    languages: list[str]
    available_data: list[str]
    integration_environment: list[str]
    current_manual_effort: list[str]
    desired_outcomes: list[str] = Field(min_length=1)


class Technographics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_posture: list[str]
    systems_of_record: list[str]
    knowledge_and_document_platforms: list[str]
    communication_stack: list[str]
    ai_maturity: str
    security_constraints: list[str]


class PainJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pain: str
    job_to_be_done: str
    business_impact: str
    evidence_ids: list[str]


class ServiceOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    use_case: str
    fit_rationale: str
    required_customer_inputs: list[str]


class BuyingRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_type: BuyingRoleType
    likely_titles: list[str] = Field(min_length=1)
    priorities: list[str]
    concerns: list[str]


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: str
    why_now: str
    freshness_window: str
    discoverability: str


class QualificationSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: str
    fit_or_intent: str = Field(pattern=r"^(fit|intent)$")
    verification_method: str


class Disqualifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str
    reason: str


class Objection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objection: str
    response_hypothesis: str


class EntryOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    scope: str
    expected_outcome: str
    required_inputs: list[str]
    delivery_window: str
    expansion_path: str


class FitScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    rationale: str
    evidence_ids: list[str]


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high_intent_signals: list[str]
    medium_intent_signals: list[str]
    low_intent_state: str


class ICPIdentity(BaseModel):
    """The campaign-segment axes used for deterministic ICP deduplication.

    Values describe a best-fit organization segment, never an individual
    company.  The host derives a canonical key from these fields; model output
    alone is not treated as a uniqueness decision.
    """

    model_config = ConfigDict(extra="forbid")

    industry: str = Field(min_length=1)
    geography: str = Field(min_length=1)
    company_size_band: str = Field(min_length=1)
    primary_workflow: str = Field(min_length=1)
    primary_buyer_role: str = Field(min_length=1)
    deployment_posture: str = Field(min_length=1)

    def segment_key(self) -> str:
        """Return a stable key for exact campaign-scoped segment exclusion."""
        values = (
            self.industry,
            self.geography,
            self.company_size_band,
            self.primary_workflow,
            self.primary_buyer_role,
            self.deployment_posture,
        )
        return "|".join(" ".join(value.casefold().split()) for value in values)


class ICPResult(BaseModel):
    """One evidence-backed organization profile for one campaign."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    icp_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str
    identity: ICPIdentity
    profile_summary: str
    firmographics: Firmographics
    operating_profile: OperatingProfile
    technographics: Technographics
    pains_and_jobs: list[PainJob] = Field(min_length=1)
    service_fit: list[ServiceOpportunity] = Field(min_length=1)
    buying_committee: list[BuyingRole] = Field(min_length=1)
    purchase_triggers: list[Trigger] = Field(min_length=1)
    qualification_signals: list[QualificationSignal] = Field(min_length=1)
    disqualifiers: list[Disqualifier]
    likely_objections: list[Objection]
    entry_offer: EntryOffer
    fit_score: FitScore
    intent_signal_model: IntentModel
    evidence: list[EvidenceItem] = Field(min_length=2)
    assumptions: list[str]
    unknowns: list[str]
    validation_questions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence_references(self) -> ICPResult:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique within an ICP.")
        referenced = {
            evidence_id for item in self.pains_and_jobs for evidence_id in item.evidence_ids
        } | set(self.fit_score.evidence_ids)
        missing = referenced - set(evidence_ids)
        if missing:
            raise ValueError(f"ICP fields reference undefined evidence: {sorted(missing)}")
        return self


class ICPRejection(BaseModel):
    """One bounded research attempt rejected by deterministic host policy."""

    model_config = ConfigDict(extra="forbid")

    attempt: int = Field(ge=1)
    reason: str = Field(min_length=1)
    icp_id: str | None = None
    segment_key: str | None = None


class ICPBatchResult(BaseModel):
    """The durable outcome of one campaign-scoped ICP research run."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    requested_count: int = Field(ge=1)
    icps: list[ICPResult]
    rejections: list[ICPRejection]
    exhaustion_reason: str | None = None

    @model_validator(mode="after")
    def validate_batch(self) -> ICPBatchResult:
        if len(self.icps) > self.requested_count:
            raise ValueError("An ICP batch cannot contain more than requested_count profiles.")
        if any(icp.campaign_id != self.campaign_id for icp in self.icps):
            raise ValueError("Every ICP in a batch must belong to the batch campaign.")
        if len({icp.identity.segment_key() for icp in self.icps}) != len(self.icps):
            raise ValueError("ICP batch identities must be distinct.")
        if len(self.icps) < self.requested_count and not self.exhaustion_reason:
            raise ValueError("A partial ICP batch must state an exhaustion_reason.")
        return self
