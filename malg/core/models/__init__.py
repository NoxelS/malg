"""Typed contracts shared by agents and deterministic processing."""

from malg.core.models.account import (
    AccountData,
    AccountIdentity,
    AccountProbeReport,
    AccountResearchResult,
    AccountValidationAssessment,
    AccountValidationOutcome,
    CheckOutcome,
    MailDomainProbeResult,
    Money,
    UrlProbeResult,
    ValidationCheck,
)
from malg.core.models.campaign import CampaignData
from malg.core.models.icp import ICPData
from malg.core.models.person import PersonData
from malg.core.models.research import ClaimProposal, FieldObservation, ResearchModel, ResearchResult

__all__ = [
    "AccountData",
    "AccountIdentity",
    "AccountProbeReport",
    "AccountResearchResult",
    "AccountValidationAssessment",
    "AccountValidationOutcome",
    "CampaignData",
    "CheckOutcome",
    "ClaimProposal",
    "FieldObservation",
    "ICPData",
    "MailDomainProbeResult",
    "Money",
    "PersonData",
    "ResearchModel",
    "ResearchResult",
    "UrlProbeResult",
    "ValidationCheck",
]
