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
from malg.core.eurostat_support import EurostatSupport
from malg.core.models.campaign import CampaignCandidate
from malg.core.persistent_memory_support import close_persistent_memory


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
                "evidence_id": "eurostat-enterprise-ai",
                "claim": "Enterprise AI adoption provides a measurable campaign-selection signal.",
                "source_type": "eurostat",
                "evidence_kind": "quantitative",
                "source_title": "Eurostat enterprise AI use",
                "source_url": "https://ec.europa.eu/eurostat/",
                "dataset_code": "isoc_eb_ai",
                "observation_date": "2025-01-01",
                "retrieved_at": "2026-09-09T10:00:00Z",
                "geography": ["Germany"],
                "unit": "percent",
                "population": "enterprises",
                "value": 20.0,
                "strength": "strong",
            },
            {
                "evidence_id": "eurostat-enterprise-cloud",
                "claim": "Enterprise cloud use is a measurable digital-readiness signal.",
                "source_type": "eurostat",
                "evidence_kind": "quantitative",
                "source_title": "Eurostat enterprise cloud use",
                "source_url": "https://ec.europa.eu/eurostat/",
                "dataset_code": "isoc_cicce_use",
                "observation_date": "2025-01-01",
                "retrieved_at": "2026-09-09T10:00:00Z",
                "geography": ["Germany"],
                "unit": "percent",
                "population": "enterprises",
                "value": 45.0,
                "strength": "moderate",
            },
        ],
        "confidence": 3,
        "assumptions": ["The selected workflows are sufficiently digitized."],
        "unknowns": ["Exact procurement timing"],
        "next_research_questions": ["Which workflow has the clearest measurable baseline?"],
    }


def test_campaign_research_agent_uses_nooa_eurostat_agent(monkeypatch) -> None:
    monkeypatch.setattr("malg.core.browser_support.create_browser_tool", lambda config: object())
    assert issubclass(CampaignResearchAgent, Agent)
    assert issubclass(CampaignResearchAgent, BrowserSupport)
    assert issubclass(CampaignResearchAgent, EurostatSupport)
    agent = CampaignResearchAgent()
    try:
        assert hasattr(agent, "browser")
        assert hasattr(agent, "web_search")
        config = get_llm_config(load_settings())
        assert agent._llm.model == f"{config.provider}/{config.model}"
        assert agent._llm.config["custom_llm_provider"] == "openai"
        assert agent._llm._http_config.read_timeout == config.request_timeout_seconds
    finally:
        close_persistent_memory(agent)


def test_campaign_research_method_returns_campaign_candidate() -> None:
    assert get_type_hints(CampaignResearchAgent.find_campaign)["return"] is CampaignCandidate


def test_campaign_research_contract_instructs_search_then_browser() -> None:
    context = CampaignResearchAgent.find_campaign.__doc__ or ""
    assert "self.web_search.search" in context
    assert "self.browser" in context
    assert "sole evidence source" in (CampaignResearchAgent.__doc__ or "")


def test_campaign_contract_rejects_duplicate_evidence_ids() -> None:
    payload = campaign_payload()
    evidence = payload["evidence"]
    assert isinstance(evidence, list)
    duplicate = dict(evidence[1])
    duplicate["evidence_id"] = "eurostat-enterprise-ai"
    evidence[1] = duplicate

    with pytest.raises(ValidationError, match="evidence_id values must be unique"):
        CampaignCandidate.model_validate(payload)


def test_campaign_contract_rejects_non_eurostat_evidence() -> None:
    payload = campaign_payload()
    evidence = payload["evidence"]
    assert isinstance(evidence, list)
    item = evidence[1]
    assert isinstance(item, dict)
    item["source_type"] = "industry"
    item.pop("dataset_code")

    with pytest.raises(ValidationError, match="Eurostat evidence only"):
        CampaignCandidate.model_validate(payload)


def test_main_searches_one_campaign_without_a_browser(monkeypatch) -> None:
    expected = CampaignCandidate.model_validate(campaign_payload())

    class FakeCampaignResearchAgent:
        async def find_campaign(self) -> CampaignCandidate:
            return expected

    monkeypatch.setattr(__main__, "CampaignResearchAgent", FakeCampaignResearchAgent)

    assert asyncio.run(__main__.find_one_campaign()) == expected
