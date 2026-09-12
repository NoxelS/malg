from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import get_type_hints

import pytest
from nooa import Agent
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from tests.test_account_artifacts import account_candidate_payload
from tests.test_campaign_research_agent import campaign_payload
from tests.test_icp_batch_runner import _icp

from malg import __main__
from malg.config import get_llm_config, load_settings
from malg.core.agents.account_research import AccountResearchAgent
from malg.core.agents.account_validation import AccountValidationAgent
from malg.core.browser_support import BrowserSupport
from malg.core.models.account import (
    AccountCandidate,
    AccountIdentity,
    AccountProbeReport,
    AccountValidationAssessment,
    AccountValidationOutcome,
    CheckOutcome,
    ValidationCheck,
)
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPResult
from malg.core.openai_llm import OpenAIChatClient
from malg.database import AccountMatch, AccountValidationRun, Base
from malg.database.artifacts import persist_icps
from malg.database.session import make_session_factory


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


def _smoke_engine() -> Engine:
    """Create an isolated in-memory database for the executable smoke test."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


def test_main_researches_and_persists_one_account_from_first_icp(monkeypatch, capsys) -> None:
    """The executable path selects one ICP, validates its account, and stores the outcome."""
    engine = _smoke_engine()
    sessions = make_session_factory(engine)
    campaign = CampaignCandidate.model_validate(campaign_payload())
    first_icp = _icp("manufacturing-ops", "Incident intake")
    later_icp = _icp("zeta-operations", "Maintenance planning")
    persist_icps(campaign, [later_icp, first_icp], sessions)

    class FakeResearchAgent:
        async def research_one(
            self,
            campaign: CampaignCandidate,
            icp: ICPResult,
            excluded_accounts: list,
        ) -> AccountCandidate:
            return AccountCandidate.model_validate(account_candidate_payload())

    class FakeValidationAgent:
        async def validate_account(
            self,
            campaign: CampaignCandidate,
            icp: ICPResult,
            candidate: AccountCandidate,
            probes,
        ) -> AccountValidationAssessment:
            return AccountValidationAssessment(
                outcome=AccountValidationOutcome.ACCEPTED,
                rationale="The deterministic smoke candidate is accepted.",
                checks=[
                    ValidationCheck(
                        check_type="identity",
                        target=candidate.identity.display_name,
                        outcome=CheckOutcome.PASS,
                        reason="The test candidate has a resolved identity.",
                    )
                ],
                validated_at=datetime(2026, 9, 12, tzinfo=UTC),
            )

    async def deterministic_probe(self, candidate: AccountCandidate):
        return __main__.AccountProbeReport()

    monkeypatch.setattr(__main__, "make_engine", lambda: engine)
    monkeypatch.setattr(__main__, "AccountResearchAgent", FakeResearchAgent)
    monkeypatch.setattr(__main__, "AccountValidationAgent", FakeValidationAgent)
    monkeypatch.setattr(__main__.AccountProbeService, "probe_candidate", deterministic_probe)
    monkeypatch.setattr(engine, "dispose", lambda: None)

    asyncio.run(__main__.main())

    result = __import__("json").loads(capsys.readouterr().out)
    assert result["icp"]["icp_id"] == first_icp.icp_id
    assert result["candidate"]["icp_id"] == first_icp.icp_id
    assert result["validation"]["outcome"] == "accepted"
    with sessions() as session:
        matches = session.scalars(select(AccountMatch)).all()
        validation_runs = session.scalars(select(AccountValidationRun)).all()
    assert len(matches) == 1
    assert len(validation_runs) == 1
    assert matches[0].status == "accepted"
    assert validation_runs[0].outcome == "accepted"


def test_load_first_persisted_icp_rejects_empty_database() -> None:
    """The smoke path fails clearly instead of falling back to a file artifact."""
    engine = _smoke_engine()
    with (
        make_session_factory(engine)() as session,
        pytest.raises(RuntimeError, match="No persisted ICPs"),
    ):
        __main__.load_first_persisted_icp(session)
