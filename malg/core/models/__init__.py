"""Typed contracts shared by MALG agents and deterministic processing."""

from malg.core.models.account import AccountFirmographics, AccountOperatingProfile, AccountProfile, AccountTechnographics
from malg.core.models.evidence import EvidenceItem, EvidenceKind, EvidenceStrength, SourceType
from malg.core.models.icp import ICPResult
from malg.core.models.market import (
    MarketDiscoveryResult,
    MarketResearchResult,
    MarketSeed,
    ScoreDimension,
    ScoredMarket,
)

__all__ = [
    "AccountFirmographics",
    "AccountOperatingProfile",
    "AccountProfile",
    "AccountTechnographics",
    "EvidenceItem",
    "EvidenceKind",
    "EvidenceStrength",
    "ICPResult",
    "MarketDiscoveryResult",
    "MarketResearchResult",
    "MarketSeed",
    "ScoreDimension",
    "ScoredMarket",
    "SourceType",
]
