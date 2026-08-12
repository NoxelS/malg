from typing import Generic, get_origin, get_type_hints, get_args

from nooa import Agent

from malg.config import get_llm_config, load_settings
from malg.core.agents.account_research import AccountResearchAgent
from malg.core.browser_support import BrowserSupport
from malg.core.eurostat_support import EurostatSupport
from malg.core.models.account import AccountProfile
from malg.core.models.icp import ICPResult
from malg.utils.decorators import use_default_llm_endpoint


def test_account_research_agent_uses_nooa_agent_model(monkeypatch) -> None:
    monkeypatch.setattr("malg.core.browser_support.create_browser_tool", lambda config: object())
    assert issubclass(AccountResearchAgent, Agent)
    assert issubclass(AccountResearchAgent, BrowserSupport)
    assert issubclass(AccountResearchAgent, EurostatSupport)
    client = AccountResearchAgent()._llm
    config = get_llm_config(load_settings())
    assert client.model == f"{config.provider}/{config.model}"
    assert client.config["custom_llm_provider"] == "openai"


def test_account_research_agent_context_defines_contract() -> None:
    context = AccountResearchAgent.__doc__ or ""
    research_context = AccountResearchAgent.research_account.__doc__ or ""

    assert "real, existing companies" in context
    assert "firmographics" in context
    assert "operating profile" in context
    assert "technographics" in context
    assert "Do not invent" in research_context
    assert "self.browser" in research_context
    assert "evidence" in research_context
    assert get_type_hints(AccountResearchAgent.research_account)["return"] == list[AccountProfile]
    hints = get_type_hints(AccountResearchAgent.research_account)
    assert hints["icp"] is ICPResult
    assert hints["region"] is str
    assert hints["top_n_accounts"] is int


def test_account_research_agent_rejects_non_icp_input(monkeypatch) -> None:
    monkeypatch.setattr("malg.core.browser_support.create_browser_tool", lambda config: object())
    context = AccountResearchAgent.research_account.__doc__ or ""
    assert "icp_id" in context
    assert "Preserve icp_id exactly" in context