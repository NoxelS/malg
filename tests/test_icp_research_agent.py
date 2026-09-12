"""Observable staged-generation behavior for ICP research."""

from __future__ import annotations

import asyncio

from tests.fixtures import _campaign, _icp

from malg.core.agents.icp_research import ICPResearchAgent
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import (
    ICPBuyerSignals,
    ICPEntryPlan,
    ICPEvidenceAssessment,
    ICPIdentity,
    ICPOperatingFoundation,
    ICPResult,
    ICPSegmentFoundation,
)


class _CompletedStages:
    def __init__(self, expected: ICPResult) -> None:
        payload = expected.model_dump()
        self.segment = ICPSegmentFoundation.model_validate(
            {name: payload[name] for name in ICPSegmentFoundation.model_fields}
        )
        self.operations = ICPOperatingFoundation.model_validate(
            {name: payload[name] for name in ICPOperatingFoundation.model_fields}
        )
        self.evidence_assessment = ICPEvidenceAssessment.model_validate(
            {name: payload[name] for name in ICPEvidenceAssessment.model_fields}
        )
        self.buyer_signals = ICPBuyerSignals.model_validate(
            {name: payload[name] for name in ICPBuyerSignals.model_fields}
        )
        self.entry_plan = ICPEntryPlan.model_validate(
            {name: payload[name] for name in ICPEntryPlan.model_fields}
        )

    async def _research_segment(
        self, campaign: CampaignCandidate, excluded_segments: list[ICPIdentity]
    ) -> ICPSegmentFoundation:
        return self.segment

    async def _research_operations(
        self, campaign: CampaignCandidate, segment: ICPSegmentFoundation
    ) -> ICPOperatingFoundation:
        return self.operations

    async def _research_evidence(
        self,
        campaign: CampaignCandidate,
        segment: ICPSegmentFoundation,
        operations: ICPOperatingFoundation,
    ) -> ICPEvidenceAssessment:
        return self.evidence_assessment

    async def _research_buyer_signals(
        self,
        campaign: CampaignCandidate,
        segment: ICPSegmentFoundation,
        evidence_assessment: ICPEvidenceAssessment,
    ) -> ICPBuyerSignals:
        return self.buyer_signals

    async def _research_entry_plan(
        self,
        campaign: CampaignCandidate,
        segment: ICPSegmentFoundation,
        operations: ICPOperatingFoundation,
        evidence_assessment: ICPEvidenceAssessment,
    ) -> ICPEntryPlan:
        return self.entry_plan


def test_research_one_assembles_validated_stages_into_canonical_result() -> None:
    expected = _icp("staged-profile", "Incident intake")
    staged = _CompletedStages(expected)

    result = asyncio.run(ICPResearchAgent.research_one(staged, _campaign(), []))  # type: ignore[arg-type]

    assert result == expected


def test_staged_contracts_cover_final_result_with_smaller_outputs() -> None:
    staged_models = (
        ICPSegmentFoundation,
        ICPOperatingFoundation,
        ICPEvidenceAssessment,
        ICPBuyerSignals,
        ICPEntryPlan,
    )
    staged_fields = set().union(*(set(model.model_fields) for model in staged_models))

    assert staged_fields == set(ICPResult.model_fields)
    assert all(len(model.model_fields) < len(ICPResult.model_fields) for model in staged_models)
