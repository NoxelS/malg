"""Structured output contract for market research."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from malg.core.models.evidence import EvidenceItem, EvidenceKind


class ScoreDimension(StrEnum):
    DEMAND_INTENSITY = "demand_intensity"
    SERVICE_FIT = "service_fit"
    DIGITAL_READINESS = "digital_readiness"
    ECONOMIC_CAPACITY = "economic_capacity"
    FREELANCER_ACCESSIBILITY = "freelancer_accessibility"
    COMPETITIVE_WHITESPACE = "competitive_whitespace"
    GEOGRAPHIC_LANGUAGE_FIT = "geographic_language_fit"
    LEAD_DISCOVERABILITY = "lead_discoverability"
    TIME_TO_FIRST_ENGAGEMENT = "time_to_first_engagement"
    REGULATORY_DELIVERY_FEASIBILITY = "regulatory_delivery_feasibility"


class ScoreComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: ScoreDimension
    score: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class MarketSeed(BaseModel):
    """Small discovery-stage description used to drive focused research."""

    model_config = ConfigDict(extra="forbid")

    market_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1)
    nace_codes: list[str] = Field(min_length=1)
    countries: list[str] = Field(min_length=1)
    opportunity_hypothesis: str = Field(min_length=1)
    research_questions: list[str] = Field(min_length=1)


class MarketDiscoveryResult(BaseModel):
    """Compact first-stage output containing market hypotheses only."""

    model_config = ConfigDict(extra="forbid")

    research_date: date
    geographic_scope: list[str] = Field(min_length=1)
    methodology_summary: str = Field(min_length=1)
    markets: list[MarketSeed] = Field(min_length=1)
    limitations: list[str]

    @model_validator(mode="after")
    def validate_market_ids(self) -> MarketDiscoveryResult:
        market_ids = [market.market_id for market in self.markets]
        if len(market_ids) != len(set(market_ids)):
            raise ValueError("market_id values must be unique.")
        return self


class ScoredMarket(BaseModel):
    """A market candidate; totals and rank are overwritten deterministically."""

    model_config = ConfigDict(extra="forbid")

    market_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1)
    nace_codes: list[str] = Field(min_length=1)
    countries: list[str] = Field(min_length=1)
    market_definition: str = Field(min_length=1)
    company_size_focus: list[str] = Field(min_length=1)
    market_characteristics: list[str] = Field(min_length=1)
    problems: list[str] = Field(min_length=1)
    applicable_services: list[str] = Field(min_length=1)
    entry_offer_hypothesis: str = Field(min_length=1)
    engagement_model: str = Field(min_length=1)
    buyer_role_hypotheses: list[str] = Field(min_length=1)
    language_access: list[str] = Field(min_length=1)
    risks: list[str] = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(min_length=2)
    score_components: list[ScoreComponent]
    total_score: float | None = Field(default=None, ge=0, le=100)
    rank: int | None = Field(default=None, ge=1)
    confidence: int = Field(ge=1, le=5)
    assumptions: list[str]
    unknowns: list[str]
    next_research_questions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence_and_dimensions(self) -> ScoredMarket:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique within a market.")
        kinds = {item.evidence_kind for item in self.evidence}
        if kinds != set(EvidenceKind):
            raise ValueError("each market requires quantitative and qualitative evidence.")

        dimensions = [component.dimension for component in self.score_components]
        expected = set(ScoreDimension)
        if len(dimensions) != len(set(dimensions)) or set(dimensions) != expected:
            raise ValueError("score_components must contain every score dimension exactly once.")

        defined = set(evidence_ids)
        missing = {evidence_id for component in self.score_components for evidence_id in component.evidence_ids if evidence_id not in defined}
        if missing:
            raise ValueError(f"score components reference undefined evidence: {sorted(missing)}")
        return self


class MarketResearchResult(BaseModel):
    """Application-assembled result containing researched and ranked markets."""

    model_config = ConfigDict(extra="forbid")

    research_date: date
    geographic_scope: list[str] = Field(min_length=1)
    methodology_summary: str = Field(min_length=1)
    scorecard_version: str = "1.0"
    markets: list[ScoredMarket] = Field(min_length=1)
    limitations: list[str]

    @model_validator(mode="after")
    def validate_market_ids(self) -> MarketResearchResult:
        market_ids = [market.market_id for market in self.markets]
        if len(market_ids) != len(set(market_ids)):
            raise ValueError("market_id values must be unique.")
        return self
