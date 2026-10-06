"""Lean account generation, identity, validation, and probe contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from malg.core.models.research import FieldObservation, ResearchResult
from malg.crm.identity import normalize_domain

# ISO 4217 alphabetic codes used by the supported currency set. Unknown codes are rejected.
_ISO4217 = frozenset(
    [
        "AED",
        "AFN",
        "ALL",
        "AMD",
        "ANG",
        "AOA",
        "ARS",
        "AUD",
        "AWG",
        "AZN",
        "BAM",
        "BBD",
        "BDT",
        "BGN",
        "BHD",
        "BIF",
        "BMD",
        "BND",
        "BOB",
        "BOV",
        "BRL",
        "BSD",
        "BTN",
        "BWP",
        "BYN",
        "BZD",
        "CAD",
        "CDF",
        "CHE",
        "CHF",
        "CHW",
        "CLF",
        "CLP",
        "CNY",
        "COP",
        "COU",
        "CRC",
        "CUC",
        "CUP",
        "CVE",
        "CZK",
        "DJF",
        "DKK",
        "DOP",
        "DZD",
        "EGP",
        "ERN",
        "ETB",
        "EUR",
        "FJD",
        "FKP",
        "GBP",
        "GEL",
        "GHS",
        "GIP",
        "GMD",
        "GNF",
        "GTQ",
        "GYD",
        "HKD",
        "HNL",
        "HTG",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "IQD",
        "IRR",
        "ISK",
        "JMD",
        "JOD",
        "JPY",
        "KES",
        "KGS",
        "KHR",
        "KMF",
        "KPW",
        "KRW",
        "KWD",
        "KYD",
        "KZT",
        "LAK",
        "LBP",
        "LKR",
        "LRD",
        "LSL",
        "LYD",
        "MAD",
        "MDL",
        "MGA",
        "MKD",
        "MMK",
        "MNT",
        "MOP",
        "MRU",
        "MUR",
        "MVR",
        "MWK",
        "MXN",
        "MXV",
        "MYR",
        "MZN",
        "NAD",
        "NGN",
        "NIO",
        "NOK",
        "NPR",
        "NZD",
        "OMR",
        "PAB",
        "PEN",
        "PGK",
        "PHP",
        "PKR",
        "PLN",
        "PYG",
        "QAR",
        "RON",
        "RSD",
        "RUB",
        "RWF",
        "SAR",
        "SBD",
        "SCR",
        "SDG",
        "SEK",
        "SGD",
        "SHP",
        "SLE",
        "SLL",
        "SOS",
        "SRD",
        "SSP",
        "STN",
        "SVC",
        "SYP",
        "SZL",
        "THB",
        "TJS",
        "TMT",
        "TND",
        "TOP",
        "TRY",
        "TTD",
        "TWD",
        "TZS",
        "UAH",
        "UGX",
        "USD",
        "USN",
        "UYI",
        "UYU",
        "UYW",
        "UZS",
        "VED",
        "VES",
        "VND",
        "VUV",
        "WST",
        "XAF",
        "XAG",
        "XAU",
        "XBA",
        "XBB",
        "XBC",
        "XBD",
        "XCD",
        "XDR",
        "XOF",
        "XPD",
        "XPF",
        "XPT",
        "XSU",
        "XTS",
        "XUA",
        "XXX",
        "YER",
        "ZAR",
        "ZMW",
        "ZWL",
    ]
)


class Money(BaseModel):
    """Exact nonnegative amount with an explicitly observed ISO 4217 currency."""

    model_config = ConfigDict(extra="forbid")
    # Equivalent decimal syntax without lookahead, which Solheim's tool grammar rejects.
    # This changes only the advertised schema; Decimal and nonnegative validation remain.
    amount: Annotated[
        Decimal,
        WithJsonSchema(
            {
                "anyOf": [
                    {"type": "number", "minimum": 0},
                    {"type": "string", "pattern": r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$"},
                ]
            },
            mode="validation",
        ),
    ] = Field(ge=0)
    currency_code: str = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_currency(self) -> Money:
        """Reject unknown or guessed currency codes."""
        if self.currency_code not in _ISO4217:
            raise ValueError("currency_code must be an ISO 4217 alphabetic code")
        return self


class AccountData(BaseModel):
    """Canonical lean Company data without host-owned IDs or relationships."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    sector: str | None = Field(default=None, min_length=1, max_length=200)
    annual_revenue: Money | None = None
    employees: int | None = Field(default=None, strict=True, ge=0)
    website: HttpUrl | None = None
    linkedin_url: HttpUrl | None = None


class AccountValidationOutcome(StrEnum):
    """Independent qualification disposition controlling Company publication."""

    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class AccountOperationalStatus(StrEnum):
    """Observed operating state; unknown never implies an active business."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class AccountEngagementSignalType(StrEnum):
    """Public invitation types that can support a human-initiated response."""

    FREELANCE_PROJECT = "freelance_project"
    SUBCONTRACTOR_REQUEST = "subcontractor_request"
    FREELANCER_POOL = "freelancer_pool"
    FREELANCE_INITIATIVE_APPLICATION = "freelance_initiative_application"


class AccountEngagementSignalStatus(StrEnum):
    """Observed availability of a public invitation at research time."""

    OPEN = "open"
    CLOSED = "closed"
    UNCERTAIN = "uncertain"


class AccountEngagementSignal(BaseModel):
    """A sourced public invitation for freelancers or subcontractors to respond."""

    model_config = ConfigDict(extra="forbid")
    signal_type: AccountEngagementSignalType
    title: str = Field(min_length=1, max_length=300)
    source_url: HttpUrl
    invited_work: str = Field(min_length=1, max_length=1000)
    response_route: str = Field(min_length=1, max_length=500)
    status: AccountEngagementSignalStatus
    published_at: date | None = None
    deadline: date | None = None


class AccountIdentity(BaseModel):
    """Bounded observed identity anchors, not generated IDs or guessed domains."""

    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    legal_form: str | None = Field(default=None, max_length=100)
    registry_jurisdiction: str | None = Field(default=None, max_length=200)
    registration_number: str | None = Field(default=None, max_length=100)
    lei: str | None = Field(default=None, max_length=20)
    vat_id: str | None = Field(default=None, max_length=100)
    official_website: HttpUrl | None = None
    official_domains: list[str] = Field(default_factory=list, max_length=10)
    operational_status: AccountOperationalStatus = AccountOperationalStatus.UNKNOWN

    @field_validator("official_domains")
    @classmethod
    def normalize_official_domains(cls, values: list[str]) -> list[str]:
        """Canonicalize each explicitly observed official domain without guessing."""
        return list(dict.fromkeys(normalize_domain(value) for value in values))

    @model_validator(mode="after")
    def require_identity_anchor(self) -> AccountIdentity:
        """Require an observed website or registry identifier for business identity."""
        if self.official_website is None and not self.registration_number:
            raise ValueError("An account requires a registry identity or an official website")
        return self


class AccountResearchResult(ResearchResult[AccountData]):
    """Lean account result with host-independent identity and qualification."""

    identity: AccountIdentity | None = None
    qualification: AccountValidationOutcome
    engagement_signal: AccountEngagementSignal | None = None
    signal_observations: list[FieldObservation] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_identity_for_data(self) -> AccountResearchResult:
        """Validate identity presence and engagement-signal observation fields."""
        if self.data is not None and self.identity is None:
            raise ValueError("account data requires an observed identity")
        if self.engagement_signal is None and self.signal_observations:
            raise ValueError("signal observations require an engagement signal")
        if self.engagement_signal is not None:
            fields = type(self.engagement_signal).model_fields
            for observation in self.signal_observations:
                if observation.field not in fields:
                    raise ValueError(
                        f"unknown engagement signal field {observation.field!r}; "
                        f"expected one of {', '.join(fields)}"
                    )
        return self


class CheckOutcome(StrEnum):
    """Deterministic probe outcome without guessing unavailable evidence."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_RUN = "not_run"


class UrlProbeResult(BaseModel):
    """One bounded read-only public URL probe and its observed outcome."""

    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    outcome: CheckOutcome
    status_code: int | None = Field(default=None, ge=100, le=599)
    final_url: HttpUrl | None = None
    checked_at: datetime
    reason: str | None = None


class MailDomainProbeResult(BaseModel):
    """DNS-only mail-domain capability observation; never a mailbox verification."""

    model_config = ConfigDict(extra="forbid")
    domain: str = Field(min_length=1)
    outcome: CheckOutcome
    accepts_mail: bool | None = None
    checked_at: datetime
    reason: str | None = None


class AccountProbeReport(BaseModel):
    """Host-owned safe probe evidence supplied to independent account validation."""

    model_config = ConfigDict(extra="forbid")
    url_checks: list[UrlProbeResult] = Field(default_factory=list)
    mail_domain_checks: list[MailDomainProbeResult] = Field(default_factory=list)


class ValidationCheck(BaseModel):
    """One independent validation finding backed by named source URLs."""

    model_config = ConfigDict(extra="forbid")
    check_type: str = Field(min_length=1)
    target: str = Field(min_length=1)
    outcome: CheckOutcome
    reason: str = Field(min_length=1)
    source_urls: list[HttpUrl] = Field(default_factory=list)


class AccountValidationAssessment(BaseModel):
    """Independent publish/review/reject decision with explicit check evidence."""

    model_config = ConfigDict(extra="forbid")
    outcome: AccountValidationOutcome
    rationale: str = Field(min_length=1)
    checks: list[ValidationCheck] = Field(min_length=1)
    validated_at: datetime


class AccountResearchFeedback(BaseModel):
    """Previous candidate disposition used to adapt discovery within one workflow."""

    model_config = ConfigDict(extra="forbid")
    stage_key: str = Field(max_length=100)
    reason_code: str = Field(max_length=64)
    candidate_name: str | None = Field(default=None, max_length=200)
    candidate_website: str | None = Field(default=None, max_length=2048)
    unknowns: list[str] = Field(default_factory=list, max_length=10)
