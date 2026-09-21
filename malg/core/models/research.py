"""Shared bounded contracts for evidence-backed research stages."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ResearchModel(BaseModel):
    """Strict base for host-validated research payloads."""
    model_config = ConfigDict(extra="forbid")


class ClaimProposal(ResearchModel):
    """Model proposal referencing host-supplied excerpt IDs."""
    text: str = Field(min_length=1, max_length=500)
    excerpt_ids: list[str] = Field(min_length=1, max_length=8)
    quote: str = Field(min_length=1, max_length=500)


class Criterion(ResearchModel):
    criterion_id: UUID
    description: str = Field(max_length=500)
    verification_hint: str = Field(max_length=500)


class CriterionAssessment(ResearchModel):
    criterion_id: UUID
    outcome: Literal["met", "not_met", "unknown"]
    claim_ids: list[UUID] = Field(default_factory=list, max_length=10)


class ResearchOutcome(ResearchModel):
    outcome: Literal["complete", "insufficient_evidence", "budget_exhausted", "not_applicable"]
    unknowns: list[str] = Field(default_factory=list, max_length=10)
    claim_ids: list[UUID] = Field(default_factory=list, max_length=10)
