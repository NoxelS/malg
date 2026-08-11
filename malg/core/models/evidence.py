"""Auditable evidence records used by market and ICP research."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class SourceType(StrEnum):
    EUROSTAT = "eurostat"
    OFFICIAL = "official"
    INDUSTRY = "industry"
    COMPANY = "company"
    OTHER = "other"


class EvidenceStrength(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class EvidenceKind(StrEnum):
    QUANTITATIVE = "quantitative"
    QUALITATIVE = "qualitative"


class EvidenceItem(BaseModel):
    """One source-backed claim, including enough metadata to audit it."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    claim: str = Field(min_length=1)
    source_type: SourceType
    evidence_kind: EvidenceKind
    source_title: str = Field(min_length=1)
    source_url: HttpUrl
    dataset_code: str | None = None
    observation_date: date | None = None
    retrieved_at: datetime
    geography: list[str] = Field(min_length=1)
    unit: str | None = None
    population: str | None = None
    value: float | str | None = None
    strength: EvidenceStrength

    @model_validator(mode="after")
    def require_dataset_for_eurostat(self) -> EvidenceItem:
        if self.source_type is SourceType.EUROSTAT and not self.dataset_code:
            raise ValueError("Eurostat evidence requires dataset_code.")
        if self.evidence_kind is EvidenceKind.QUANTITATIVE and self.value is None:
            raise ValueError("Quantitative evidence requires value.")
        return self
