"""Offline outcomes for durable, campaign-scoped ICP batch research."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from tests.test_campaign_research_agent import campaign_payload

from malg import __main__
from malg.config import ICPConfig
from malg.core import icp_runner
from malg.core.agents.icp_research import ICPResearchAgent
from malg.core.browser_support import BrowserSupport
from malg.core.eurostat_support import EurostatSupport
from malg.core.icp_history import ICPHistoryLedger
from malg.core.icp_runner import research_icps
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPResult
from malg.database import ICP, Base
from malg.database.artifacts import persist_icps
from malg.database.session import make_session_factory


def _campaign() -> CampaignCandidate:
    return CampaignCandidate.model_validate(campaign_payload())


def _icp(icp_id: str, workflow: str) -> ICPResult:
    campaign = _campaign()
    return ICPResult.model_validate(
        {
            "campaign_id": campaign.campaign_id,
            "icp_id": icp_id,
            "title": f"{workflow} profile",
            "identity": {
                "industry": "Infrastructure services",
                "geography": "Germany",
                "company_size_band": "50-500 employees",
                "primary_workflow": workflow,
                "primary_buyer_role": "Head of Operations",
                "deployment_posture": "Private cloud",
            },
            "profile_summary": "A distinct evidence-backed organization segment.",
            "firmographics": {
                "industries": ["Infrastructure services"],
                "nace_codes": ["NACE C"],
                "countries": ["Germany"],
                "employee_range": "50-500 employees",
                "ownership_and_stage": ["Established"],
                "operating_footprint": ["Germany"],
                "regulated_data_exposure": ["Operational data"],
            },
            "operating_profile": {
                "target_workflows": [workflow],
                "work_volume_signals": ["Repeated incidents"],
                "languages": ["German"],
                "available_data": ["Operational records"],
                "integration_environment": ["Internal systems"],
                "current_manual_effort": ["Manual triage"],
                "desired_outcomes": ["Faster response"],
            },
            "technographics": {
                "deployment_posture": ["Private cloud"],
                "systems_of_record": ["Internal system"],
                "knowledge_and_document_platforms": ["Document repository"],
                "communication_stack": ["Email"],
                "ai_maturity": "Early",
                "security_constraints": ["Data residency"],
            },
            "pains_and_jobs": [
                {
                    "pain": "Manual triage",
                    "job_to_be_done": "Route incidents consistently",
                    "business_impact": "Delayed response",
                    "evidence_ids": ["eurostat-enterprise-ai"],
                }
            ],
            "service_fit": [
                {
                    "service": "Private RAG",
                    "use_case": workflow,
                    "fit_rationale": "Data remains controlled.",
                    "required_customer_inputs": ["Knowledge base"],
                }
            ],
            "buying_committee": [
                {
                    "role_type": "economic_buyer",
                    "likely_titles": ["Head of Operations"],
                    "priorities": ["Response time"],
                    "concerns": ["Data residency"],
                }
            ],
            "purchase_triggers": [
                {
                    "signal": "Incident backlog",
                    "why_now": "Operational pressure",
                    "freshness_window": "Current quarter",
                    "discoverability": "Public operational reporting",
                }
            ],
            "qualification_signals": [
                {
                    "signal": "Controlled operational data",
                    "fit_or_intent": "fit",
                    "verification_method": "Discovery call",
                }
            ],
            "disqualifiers": [],
            "likely_objections": [],
            "entry_offer": {
                "name": "Assessment",
                "scope": "One workflow",
                "expected_outcome": "Validated baseline",
                "required_inputs": ["Sample records"],
                "delivery_window": "Four weeks",
                "expansion_path": "Pilot",
            },
            "fit_score": {
                "score": 4,
                "rationale": "Workflow matches the campaign.",
                "evidence_ids": ["eurostat-enterprise-cloud"],
            },
            "intent_signal_model": {
                "high_intent_signals": ["Active review"],
                "medium_intent_signals": ["Documented pain"],
                "low_intent_state": "No public signal",
            },
            "evidence": campaign.evidence[:2],
            "assumptions": [],
            "unknowns": [],
            "validation_questions": ["Which workflow is first?"],
        }
    )


class _FakeResearcher:
    def __init__(self, candidates: list[ICPResult]) -> None:
        self.candidates = iter(candidates)
        self.exclusion_cards: list[list[str]] = []

    async def research_one(self, campaign, excluded_segments):  # type: ignore[no-untyped-def]
        self.exclusion_cards.append([identity.segment_key() for identity in excluded_segments])
        return next(self.candidates)


class _ConcurrentFakeResearcher:
    active = 0
    maximum_active = 0
    calls = 0

    async def research_one(self, campaign, excluded_segments):  # type: ignore[no-untyped-def]
        type(self).calls += 1
        call = type(self).calls
        type(self).active += 1
        type(self).maximum_active = max(type(self).maximum_active, type(self).active)
        try:
            await asyncio.sleep(0)
            return _icp(f"parallel-{call}", f"Workflow {call}")
        finally:
            type(self).active -= 1


def _config(tmp_path: Path, *, batch_size: int = 2, concurrency: int = 1) -> ICPConfig:
    return ICPConfig(
        batch_size=batch_size,
        concurrency=concurrency,
        max_attempts_per_slot=2,
        max_exclusion_cards=10,
        output_root=tmp_path / "results",
    )


def test_icp_research_agent_exposes_only_browser_research_support() -> None:
    """Keep ICP research bounded to SearXNG discovery and Lightpanda browsing."""
    assert issubclass(ICPResearchAgent, BrowserSupport)
    assert not issubclass(ICPResearchAgent, EurostatSupport)


def test_runner_retries_duplicate_and_persists_distinct_batch(tmp_path: Path) -> None:
    ledger = ICPHistoryLedger(tmp_path / "history.sqlite")
    researcher = _FakeResearcher(
        [
            _icp("first", "Incident intake"),
            _icp("first", "Incident intake"),
            _icp("second", "Knowledge retrieval"),
        ]
    )
    try:
        result = asyncio.run(
            research_icps(_campaign(), config=_config(tmp_path), history=ledger, agent=researcher)
        )
    finally:
        ledger.close()

    assert [icp.icp_id for icp in result.icps] == ["first", "second"]
    assert len(result.rejections) == 1
    assert "already exists" in result.rejections[0].reason
    assert researcher.exclusion_cards[1] == [
        _icp("first", "Incident intake").identity.segment_key()
    ]
    assert (
        tmp_path / "results" / "icps" / _campaign().campaign_id / result.run_id / "batch.json"
    ).exists()


def test_runner_calls_storage_for_each_accepted_icp(tmp_path: Path) -> None:
    """Make each accepted ICP durable before researching the next batch slot."""
    stored_ids: list[str] = []
    ledger = ICPHistoryLedger(tmp_path / "history.sqlite")
    try:
        result = asyncio.run(
            research_icps(
                _campaign(),
                config=_config(tmp_path),
                history=ledger,
                agent=_FakeResearcher(
                    [_icp("first", "Incident intake"), _icp("second", "Knowledge retrieval")]
                ),
                on_accepted=lambda icp: stored_ids.append(icp.icp_id),
            )
        )
    finally:
        ledger.close()

    assert stored_ids == ["first", "second"]
    assert [icp.icp_id for icp in result.icps] == stored_ids


def test_runner_limits_default_agents_to_configured_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run independent generated-agent instances without exceeding the configured limit."""
    _ConcurrentFakeResearcher.active = 0
    _ConcurrentFakeResearcher.maximum_active = 0
    _ConcurrentFakeResearcher.calls = 0
    monkeypatch.setattr(icp_runner, "ICPResearchAgent", _ConcurrentFakeResearcher)
    ledger = ICPHistoryLedger(tmp_path / "history.sqlite")
    try:
        result = asyncio.run(
            research_icps(
                _campaign(),
                config=_config(tmp_path, batch_size=4, concurrency=3),
                history=ledger,
            )
        )
    finally:
        ledger.close()

    assert len(result.icps) == 4
    assert _ConcurrentFakeResearcher.maximum_active == 3


def test_runner_releases_claim_when_per_icp_storage_fails(tmp_path: Path) -> None:
    """Allow a later run to retry an ICP whose durable store failed."""
    path = tmp_path / "history.sqlite"
    candidate = _icp("first", "Incident intake")
    failed_ledger = ICPHistoryLedger(path)
    try:
        with pytest.raises(RuntimeError, match="store failed"):
            asyncio.run(
                research_icps(
                    _campaign(),
                    config=_config(tmp_path, batch_size=1),
                    history=failed_ledger,
                    agent=_FakeResearcher([candidate]),
                    on_accepted=lambda _: (_ for _ in ()).throw(RuntimeError("store failed")),
                )
            )
    finally:
        failed_ledger.close()

    retry_ledger = ICPHistoryLedger(path)
    try:
        retried = asyncio.run(
            research_icps(
                _campaign(),
                config=_config(tmp_path, batch_size=1),
                history=retry_ledger,
                agent=_FakeResearcher([candidate]),
            )
        )
    finally:
        retry_ledger.close()

    assert [icp.icp_id for icp in retried.icps] == [candidate.icp_id]


def test_fresh_runner_uses_same_ledger_to_exclude_prior_batch(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite"
    first_ledger = ICPHistoryLedger(path)
    try:
        first = asyncio.run(
            research_icps(
                _campaign(),
                config=_config(tmp_path),
                history=first_ledger,
                agent=_FakeResearcher(
                    [_icp("first", "Incident intake"), _icp("second", "Knowledge retrieval")]
                ),
            )
        )
    finally:
        first_ledger.close()

    second_ledger = ICPHistoryLedger(path)
    second_agent = _FakeResearcher(
        [_icp("third", "Compliance reporting"), _icp("fourth", "Maintenance support")]
    )
    try:
        second = asyncio.run(
            research_icps(
                _campaign(), config=_config(tmp_path), history=second_ledger, agent=second_agent
            )
        )
    finally:
        second_ledger.close()

    assert {icp.icp_id for icp in first.icps}.isdisjoint(icp.icp_id for icp in second.icps)
    assert len(second_agent.exclusion_cards[0]) == 2


def test_main_loads_the_saved_campaign_artifact(tmp_path: Path) -> None:
    path = tmp_path / "1.json"
    path.write_text(_campaign().model_dump_json(), encoding="utf-8")

    assert __main__.load_saved_campaign(path).campaign_id == _campaign().campaign_id


def test_main_persistence_stores_generated_icps_with_the_saved_campaign() -> None:
    """Persisting a run retains canonical ICP payloads without campaign research."""
    engine: Engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: sqlite3.Connection, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    campaign = _campaign()
    expected = _icp("manufacturing-ops", "Incident intake")
    persist_icps(campaign, [expected], make_session_factory(engine))

    with make_session_factory(engine)() as session:
        stored = session.scalar(select(ICP))
        assert stored is not None
        assert stored.payload == expected.model_dump(mode="json")
        assert stored.campaign_id == campaign.campaign_id
