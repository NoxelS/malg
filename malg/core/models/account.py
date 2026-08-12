"""Structured output contract for company-level account research."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from malg.core.models.icp import PainJob
from malg.core.models.evidence import EvidenceItem


class AccountFirmographics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1)
    industry: str = Field(min_length=1)
    nace_codes: list[str] | None = None
    employee_range: str
    turnover_range: str | None = None
    headquarters: str = Field(min_length=1)
    operating_regions: list[str] = Field(min_length=1)
    ownership_stage: str = Field(min_length=1)
    website: str | None = None


class AccountOperatingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_workflows: list[str] = Field(min_length=1)
    tech_stack: list[str]
    current_tools: list[str]
    languages: list[str]
    data_sources: list[str]
    pain_points_with_current_setup: list[str]


class AccountTechnographics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_posture: list[str]
    erp_system: str | None = None
    crm_system: str | None = None
    ai_stack: list[str]
    security_requirements: list[str]


class AccountProfile(BaseModel):
    """One evidence-backed company profile matching an organization-level ICP."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    icp_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    region: str = Field(min_length=1)

    firmographics: AccountFirmographics
    operating_profile: AccountOperatingProfile
    technographics: AccountTechnographics
    pains_and_jobs: list[PainJob] = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(min_length=2)
    assumptions: list[str]
    unknowns: list[str]
    validation_questions: list[str] = Field(min_length=1)