from __future__ import annotations

import asyncio
from typing import get_type_hints

import pytest
from nooa import Agent
from pydantic import ValidationError

from malg import __main__
from malg.config import get_llm_config, load_settings
from malg.core.agents.campaign_research import CampaignResearchAgent
from malg.core.browser_support import BrowserSupport
from malg.core.models.campaign import CampaignCandidate


def campaign_payload() -> dict[str, object]:
    return {
        "campaign_id": "dach-private-ai-critical-workflows",
        "title": "Private AI for critical DACH workflows",
        "positioning": "Governed private AI for sensitive operational workflows.",
        "geographies": ["Germany", "Austria", "Switzerland"],
        "industries": ["Infrastructure services", "Security services"],
        "company_size_focus": ["50-500 employees"],
        "target_workflows": ["After-hours incident intake", "Internal knowledge retrieval"],
        "problem_statement": "Critical workflows need reliable context without uncontrolled data exposure.",
        "why_now": "Data-control and operational-efficiency constraints make bounded pilots relevant.",
        "buyer_role_hypotheses": ["Head of Operations", "IT or Security lead"],
        "qualification_signals": ["24/7 operation", "Sensitive operational data"],
        "exclusions": ["No digitized workflow", "Generic AI-training request"],
        "entry_offer_hypothesis": "A governed workflow assessment leading to a scoped private-AI pilot.",
        "evidence": [
            {
                "evidence_id": "portfolio-private-ai",
                "claim": "The portfolio describes private AI for sensitive European workloads.",
                "source_type": "official",
                "evidence_kind": "qualitative",
                "source_title": "Noel Schwabenland portfolio",
                "source_url": "https://portfolio-staging.noel.fyi/en/",
                "retrieved_at": "2026-09-09T10:00:00Z",
                "geography": ["Europe"],
                "strength": "strong",
            },
            {
                "evidence_id": "private-ai-market-signal",
                "claim": "Private AI can address data-control constraints in sensitive operations.",
                "source_type": "industry",
                "evidence_kind": "qualitative",
                "source_title": "Industry research",
                "source_url": "https://example.com/private-ai-research",
                "retrieved_at": "2026-09-09T10:00:00Z",
                "geography": ["DACH"],
                "strength": "moderate",
            },
        ],
        "confidence": 3,
        "assumptions": ["The selected workflows are sufficiently digitized."],
        "unknowns": ["Exact procurement timing"],
        "next_research_questions": ["Which workflow has the clearest measurable baseline?"],
    }


def test_campaign_research_agent_uses_nooa_browser_agent(monkeypatch) -> None:
    monkeypatch.setattr("malg.core.browser_support.create_browser_tool", lambda config: object())

    assert issubclass(CampaignResearchAgent, Agent)
    assert issubclass(CampaignResearchAgent, BrowserSupport)
    client = CampaignResearchAgent()._llm
    config = get_llm_config(load_settings())
    assert client.model == f"{config.provider}/{config.model}"
    assert client.config["custom_llm_provider"] == "openai"


def test_campaign_research_instruction_has_the_single_campaign_boundary() -> None:
    context = CampaignResearchAgent.__doc__ or ""
    research_context = CampaignResearchAgent.find_campaign.__doc__ or ""

    assert "production-ready RAG, voice, agent, and private/on-premise AI" in context
    assert "exactly one" in research_context
    assert "Do not discover or name individual companies" in research_context
    assert "contacts, leads, or" in research_context
    assert "prospects." in research_context
    assert "Do not create ICPs" in research_context
    assert "self.browser" in research_context
    assert get_type_hints(CampaignResearchAgent.find_campaign)["return"] is CampaignCandidate


def test_campaign_contract_rejects_duplicate_evidence_ids() -> None:
    payload = campaign_payload()
    evidence = payload["evidence"]
    assert isinstance(evidence, list)
    duplicate = dict(evidence[1])
    duplicate["evidence_id"] = "portfolio-private-ai"
    evidence[1] = duplicate

    with pytest.raises(ValidationError, match="evidence_id values must be unique"):
        CampaignCandidate.model_validate(payload)


def test_campaign_contract_requires_current_qualitative_evidence() -> None:
    payload = campaign_payload()
    evidence = payload["evidence"]
    assert isinstance(evidence, list)
    for item in evidence:
        assert isinstance(item, dict)
        item["evidence_kind"] = "quantitative"
        item["value"] = 1

    with pytest.raises(ValidationError, match="current qualitative evidence"):
        CampaignCandidate.model_validate(payload)


def test_main_searches_one_campaign_and_closes_its_browser(monkeypatch) -> None:
    expected = CampaignCandidate.model_validate(campaign_payload())
    browser = object()
    closed: list[object] = []

    class FakeCampaignResearchAgent:
        def __init__(self) -> None:
            self.browser = browser

        async def find_campaign(self) -> CampaignCandidate:
            return expected

    async def fake_close(value: object) -> None:
        closed.append(value)

    monkeypatch.setattr(__main__, "CampaignResearchAgent", FakeCampaignResearchAgent)
    monkeypatch.setattr(__main__, "aclose_browser", fake_close)

    assert asyncio.run(__main__.find_one_campaign()) == expected
    assert closed == [browser]
