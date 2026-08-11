from typing import get_type_hints

from nooa import Agent

from malg.config import get_llm_config, load_settings
from malg.core.agents.icp_research import ICPResearchAgent
from malg.core.agents.market_research import MarketResearchAgent
from malg.core.browser_support import BrowserSupport
from malg.core.eurostat_support import EurostatSupport
from malg.core.models.icp import ICPResult
from malg.core.models.market import MarketDiscoveryResult, MarketSeed, ScoredMarket
from malg.utils.decorators import use_default_llm_endpoint


def test_market_research_agent_uses_nooa_agent_model(monkeypatch) -> None:
    monkeypatch.setattr("malg.core.browser_support.create_browser_tool", lambda config: object())
    assert issubclass(MarketResearchAgent, Agent)
    assert issubclass(MarketResearchAgent, BrowserSupport)
    assert issubclass(MarketResearchAgent, EurostatSupport)
    client = MarketResearchAgent()._llm
    config = get_llm_config(load_settings())
    assert client.model == f"{config.provider}/{config.model}"
    assert client.config["custom_llm_provider"] == "openai"


def test_market_research_agent_context_defines_segment_research_contract() -> None:
    context = MarketResearchAgent.__doc__ or ""
    discovery_context = MarketResearchAgent.discover.__doc__ or ""
    research_context = MarketResearchAgent.research_market.__doc__ or ""

    assert "on behalf of the user" in context
    assert "RAG" in context
    assert "ASR" in context
    assert "TTS" in context
    assert "Europe broadly" in discovery_context
    assert "lightweight discovery step" in discovery_context
    assert "individual companies" in research_context
    assert "Eurostat" in research_context
    assert "DataFrames" in research_context
    assert "self.browser" in research_context
    assert get_type_hints(MarketResearchAgent.discover)["return"] is MarketDiscoveryResult
    hints = get_type_hints(MarketResearchAgent.research_market)
    assert hints["market"] is MarketSeed
    assert hints["return"] is ScoredMarket


def test_default_endpoint_decorator_accepts_a_model_override() -> None:
    @use_default_llm_endpoint(model="custom-model")
    class CustomModelAgent(Agent):
        pass

    client = CustomModelAgent()._llm
    assert client.model == f"{get_llm_config(load_settings()).provider}/custom-model"
    assert client.config["custom_llm_provider"] == "openai"


def test_icp_agent_uses_a_structured_return_contract() -> None:
    context = ICPResearchAgent.__doc__ or ""
    research_context = ICPResearchAgent.research.__doc__ or ""

    assert "organization profile" in context
    assert "Do not name individual" in research_context
    assert get_type_hints(ICPResearchAgent.research)["return"] is ICPResult
