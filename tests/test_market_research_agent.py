from nooa import Agent

from malg.config import get_llm_config, load_settings
from malg.core.agents.market_research import MarketResearchAgent
from malg.utils.decorators import use_default_llm_endpoint


def test_market_research_agent_uses_nooa_agent_model() -> None:
    assert issubclass(MarketResearchAgent, Agent)
    client = MarketResearchAgent()._llm
    assert client.model == get_llm_config(load_settings()).model
    assert client.config["custom_llm_provider"] == "openai"


def test_default_endpoint_decorator_accepts_a_model_override() -> None:
    @use_default_llm_endpoint(model="custom-model")
    class CustomModelAgent(Agent):
        pass

    client = CustomModelAgent()._llm
    assert client.model == "custom-model"
    assert client.config["custom_llm_provider"] == "openai"
