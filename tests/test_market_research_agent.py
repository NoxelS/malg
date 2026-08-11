from nooa import Agent

from malg.config import get_llm_config, load_settings
from malg.core.agents.market_research import MarketResearchAgent
from malg.core.browser_support import BrowserSupport
from malg.core.eurostat_support import EurostatSupport
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
    research_context = MarketResearchAgent.research.__doc__ or ""

    assert "on behalf of the user" in context
    assert "RAG" in context
    assert "ASR" in context
    assert "TTS" in context
    assert "Europe broadly" in research_context
    assert "market segments only" in research_context
    assert "individual companies" in research_context
    assert "Eurostat DataFrames" in research_context
    assert "self.browser" in research_context


def test_default_endpoint_decorator_accepts_a_model_override() -> None:
    @use_default_llm_endpoint(model="custom-model")
    class CustomModelAgent(Agent):
        pass

    client = CustomModelAgent()._llm
    assert client.model == f"{get_llm_config(load_settings()).provider}/custom-model"
    assert client.config["custom_llm_provider"] == "openai"
