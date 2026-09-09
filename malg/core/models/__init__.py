"""Typed contracts shared by MALG agents and deterministic processing."""

from malg.core.models.account import AccountFirmographics, AccountOperatingProfile, AccountProfile, AccountTechnographics
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.evidence import EvidenceItem, EvidenceKind, EvidenceStrength, SourceType
from malg.core.models.icp import ICPResult

__all__ = [
    "AccountFirmographics",
    "AccountOperatingProfile",
    "AccountProfile",
    "AccountTechnographics",
    "CampaignCandidate",
    "EvidenceItem",
    "EvidenceKind",
    "EvidenceStrength",
    "ICPResult",
    "SourceType",
]
