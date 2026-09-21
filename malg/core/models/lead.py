"""Bounded project, contact, review, and lead records."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LeadModel(BaseModel):
    """Strict base for host-owned lead stage payloads."""
    model_config = ConfigDict(extra="forbid")


class ProjectIdea(LeadModel):
    project_id: UUID
    account_match_id: UUID
    observation_claim_ids: list[UUID] = Field(min_length=1, max_length=10)
    hypothesis: str = Field(max_length=1000)
    pilot_scope: str = Field(max_length=1000)
    expected_outcome: str = Field(max_length=1000)
    prerequisites: list[str] = Field(default_factory=list, max_length=10)
    validation_questions: list[str] = Field(default_factory=list, max_length=10)
    opportunity_confidence: Literal["unknown", "low", "medium"]
    unknowns: list[str] = Field(default_factory=list, max_length=10)


class Contact(LeadModel):
    full_name: str = Field(max_length=500)
    title: str = Field(max_length=500)
    employment_claim_ids: list[UUID] = Field(min_length=1, max_length=10)
    relevance: str = Field(max_length=1000)
    relevance_claim_ids: list[UUID] = Field(min_length=1, max_length=10)
    employment_status: Literal["current", "uncertain"]


class ContactOutcome(LeadModel):
    status: Literal["named_contact", "organization_channel_only", "no_public_contact"]
    contacts: list[Contact] = Field(default_factory=list, max_length=3)
    organization_endpoints: list[str] = Field(default_factory=list, max_length=10)
    unknowns: list[str] = Field(default_factory=list, max_length=10)


class ClaimCheck(LeadModel):
    claim_id: UUID
    verdict: Literal["supported", "unsupported", "inconclusive"]
    reason: str = Field(max_length=500)
    missing_evidence: list[str] = Field(default_factory=list, max_length=10)


class ClaimReview(LeadModel):
    checks: list[ClaimCheck] = Field(max_length=10)
    followup_requests: list[str] = Field(default_factory=list, max_length=3)


class LeadRecord(LeadModel):
    lead_id: UUID
    workflow_id: UUID
    completeness: Literal["complete", "missing_project", "missing_named_contact", "missing_project_and_contact", "unqualified"]
    review_status: Literal["pending", "accepted", "rejected"] = "pending"
    limitations: list[str] = Field(default_factory=list, max_length=10)
