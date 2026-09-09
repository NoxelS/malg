"""Structured contract for the first campaign-discovery step."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from malg.core.models.evidence import EvidenceItem, EvidenceKind


class CampaignCandidate(BaseModel):
    """One evidence-backed campaign boundary to validate before ICP research."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1)
    positioning: str = Field(min_length=1)
    geographies: list[str] = Field(min_length=1)
    industries: list[str] = Field(min_length=1)
    company_size_focus: list[str] = Field(min_length=1)
    target_workflows: list[str] = Field(min_length=1)
    problem_statement: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    buyer_role_hypotheses: list[str] = Field(min_length=1)
    qualification_signals: list[str] = Field(min_length=1)
    exclusions: list[str] = Field(min_length=1)
    entry_offer_hypothesis: str = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(min_length=2)
    confidence: int = Field(ge=1, le=5)
    assumptions: list[str]
    unknowns: list[str]
    next_research_questions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence(self) -> CampaignCandidate:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique within a campaign.")
        if EvidenceKind.QUALITATIVE not in {item.evidence_kind for item in self.evidence}:
            raise ValueError("a campaign requires current qualitative evidence.")
        return self
