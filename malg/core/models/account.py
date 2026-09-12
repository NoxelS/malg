"""Typed account, contact, endpoint, and validation contracts.

Agents return evidence-backed candidates. Host-owned persistence assigns stable
identifiers, deduplicates organisations, runs deterministic probes, and decides
whether a candidate may enter the human review queue.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from malg.core.models.evidence import EvidenceItem


class AccountOperationalStatus(StrEnum):
    """Observed legal or operating state of an organisation."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class EndpointKind(StrEnum):
    """A public business communication channel that MALG may record."""

    EMAIL = "email"
    LINKEDIN = "linkedin"
    CONTACT_FORM = "contact_form"
    PHONE = "phone"


class DiscoveryMethod(StrEnum):
    """How an endpoint value entered MALG."""

    PUBLISHED = "published"
    INFERRED = "inferred"
    MANUAL = "manual"


class CheckOutcome(StrEnum):
    """The three-valued result used by deterministic and agent checks."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_RUN = "not_run"


class AccountValidationOutcome(StrEnum):
    """Host-facing disposition for one campaign and ICP account candidate."""

    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class AccountIdentity(BaseModel):
    """Evidence-backed identifiers and official web presence for one organisation."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1)
    legal_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    legal_form: str | None = None
    registry_jurisdiction: str | None = None
    registration_number: str | None = None
    lei: str | None = None
    vat_id: str | None = None
    operational_status: AccountOperationalStatus = AccountOperationalStatus.UNKNOWN
    registered_office: str | None = None
    headquarters: str | None = None
    official_website: HttpUrl | None = None
    official_domains: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_identity_anchor(self) -> AccountIdentity:
        """Require a registry anchor or official website for a usable candidate."""
        has_registry = bool(self.registry_jurisdiction and self.registration_number)
        if not has_registry and self.official_website is None:
            raise ValueError("An account requires a registry identity or an official website.")
        return self


class AccountFirmographics(BaseModel):
    """Observed organisation-level attributes that support ICP fit assessment."""

    model_config = ConfigDict(extra="forbid")

    industries: list[str] = Field(min_length=1)
    nace_codes: list[str] = Field(default_factory=list)
    employee_range: str | None = None
    turnover_range: str | None = None
    operating_regions: list[str] = Field(default_factory=list)
    ownership_or_group: str | None = None


class AccountOperatingProfile(BaseModel):
    """Evidence-backed operating and technical observations about an account."""

    model_config = ConfigDict(extra="forbid")

    key_workflows: list[str] = Field(default_factory=list)
    current_tools: list[str] = Field(default_factory=list)
    deployment_posture: list[str] = Field(default_factory=list)
    security_requirements: list[str] = Field(default_factory=list)


class AccountFitAssessment(BaseModel):
    """Why one organisation does or does not match the supplied campaign ICP."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1)
    matched_attributes: list[str] = Field(default_factory=list)
    disqualifiers: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)


class CommunicationEndpointCandidate(BaseModel):
    """One sourced public business endpoint before host-owned verification."""

    model_config = ConfigDict(extra="forbid")

    kind: EndpointKind
    value: str = Field(min_length=1, max_length=2048)
    discovery_method: DiscoveryMethod
    source_url: HttpUrl
    source_title: str = Field(min_length=1)
    observed_at: datetime

    @model_validator(mode="after")
    def prohibit_inferred_linkedin(self) -> CommunicationEndpointCandidate:
        """A LinkedIn URL must be observed, never constructed from a name."""
        if (
            self.kind is EndpointKind.LINKEDIN
            and self.discovery_method is not DiscoveryMethod.PUBLISHED
        ):
            raise ValueError("LinkedIn endpoints must be published by a source or added manually.")
        return self


class ContactCandidate(BaseModel):
    """A public professional contact associated with the candidate organisation."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    buyer_role: str | None = None
    employment_source_url: HttpUrl
    employment_source_title: str = Field(min_length=1)
    observed_at: datetime
    endpoints: list[CommunicationEndpointCandidate] = Field(default_factory=list)


class AccountCandidate(BaseModel):
    """One agent-produced account candidate scoped to a campaign and ICP.

    This is not a durable account identity. The host assigns the account ID,
    resolves duplicates, and stores the candidate payload after validation.
    """

    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    icp_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    identity: AccountIdentity
    firmographics: AccountFirmographics
    operating_profile: AccountOperatingProfile
    fit: AccountFitAssessment
    account_endpoints: list[CommunicationEndpointCandidate] = Field(default_factory=list)
    contacts: list[ContactCandidate] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(min_length=2)
    assumptions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_references(self) -> AccountCandidate:
        """Require unique evidence and resolve the ICP-fit evidence references."""
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique within an account candidate.")
        missing = set(self.fit.evidence_ids) - set(evidence_ids)
        if missing:
            raise ValueError(f"Account fit references undefined evidence: {sorted(missing)}")
        return self


class UrlProbeResult(BaseModel):
    """A bounded HTTP observation made outside an agent context."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    outcome: CheckOutcome
    status_code: int | None = Field(default=None, ge=100, le=599)
    final_url: HttpUrl | None = None
    checked_at: datetime
    reason: str | None = None


class MailDomainProbeResult(BaseModel):
    """A domain-level mail-routing observation, never a mailbox assertion."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1)
    outcome: CheckOutcome
    accepts_mail: bool | None = None
    checked_at: datetime
    reason: str | None = None


class AccountProbeReport(BaseModel):
    """Deterministic observations supplied to the independent validator agent."""

    model_config = ConfigDict(extra="forbid")

    url_checks: list[UrlProbeResult] = Field(default_factory=list)
    mail_domain_checks: list[MailDomainProbeResult] = Field(default_factory=list)


class ValidationCheck(BaseModel):
    """One auditable assertion made during account validation."""

    model_config = ConfigDict(extra="forbid")

    check_type: str = Field(min_length=1)
    target: str = Field(min_length=1)
    outcome: CheckOutcome
    reason: str = Field(min_length=1)
    source_urls: list[HttpUrl] = Field(default_factory=list)


class AccountValidationAssessment(BaseModel):
    """Independent validation result for one candidate without any side effects."""

    model_config = ConfigDict(extra="forbid")

    outcome: AccountValidationOutcome
    rationale: str = Field(min_length=1)
    checks: list[ValidationCheck] = Field(min_length=1)
    validated_at: datetime


class AccountRecord(BaseModel):
    """Host-persisted, workspace-global account identity and current profile."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    identity: AccountIdentity
    firmographics: AccountFirmographics
    operating_profile: AccountOperatingProfile
    created_at: datetime
    updated_at: datetime


class AccountMatchRecord(BaseModel):
    """A campaign/ICP-scoped account candidate and its latest validation result."""

    model_config = ConfigDict(extra="forbid")

    account_match_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    account_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    candidate: AccountCandidate
    validation: AccountValidationAssessment | None = None
    created_at: datetime
    updated_at: datetime


# Compatibility alias while callers migrate from the unpersisted prototype name.
AccountProfile = AccountCandidate
