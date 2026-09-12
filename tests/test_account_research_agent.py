from typing import get_type_hints

from nooa import Agent

from malg.config import get_llm_config, load_settings
from malg.core.agents.account_research import AccountResearchAgent
from malg.core.agents.account_validation import AccountValidationAgent
from malg.core.browser_support import BrowserSupport
from malg.core.models.account import (
    AccountCandidate,
    AccountIdentity,
    AccountProbeReport,
    AccountValidationAssessment,
)
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPResult
from malg.core.openai_llm import OpenAIChatClient


def test_account_research_agent_uses_nooa_agent_model(monkeypatch) -> None:
    monkeypatch.setattr("malg.core.browser_support.create_browser_tool", lambda config: object())
    assert issubclass(AccountResearchAgent, Agent)
    assert issubclass(AccountResearchAgent, BrowserSupport)
    client = AccountResearchAgent()._llm
    config = get_llm_config(load_settings())
    assert isinstance(client, OpenAIChatClient)
    assert client.model == config.model


def test_account_agents_have_typed_side_effect_free_contracts(monkeypatch) -> None:
    monkeypatch.setattr("malg.core.browser_support.create_browser_tool", lambda config: object())
    assert issubclass(AccountValidationAgent, Agent)
    assert issubclass(AccountValidationAgent, BrowserSupport)

    hints = get_type_hints(AccountResearchAgent.research_one)
    assert hints["campaign"] is CampaignCandidate
    assert hints["icp"] is ICPResult
    assert hints["excluded_accounts"] == list[AccountIdentity]
    assert hints["return"] is AccountCandidate

    validation_hints = get_type_hints(AccountValidationAgent.validate_account)
    assert validation_hints["candidate"] is AccountCandidate
    assert validation_hints["probes"] is AccountProbeReport
    assert validation_hints["return"] is AccountValidationAssessment
