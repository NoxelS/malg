from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from malg.core.models.icp import ICPResult
from malg.core.models.market import MarketResearchResult, ScoreDimension, ScoredMarket
from malg.core.results import render_icp_markdown, write_icp_result
from malg.core.scoring import SCORECARD_VERSION, calculate_market_score, rank_markets


def evidence(evidence_id: str, *, source_type: str = "official") -> dict[str, object]:
    item: dict[str, object] = {
        "evidence_id": evidence_id,
        "claim": f"Claim for {evidence_id}",
        "source_type": source_type,
        "evidence_kind": "quantitative" if source_type == "eurostat" else "qualitative",
        "source_title": "Primary source",
        "source_url": "https://example.com/source",
        "observation_date": "2025-01-01",
        "retrieved_at": "2026-08-11T10:00:00Z",
        "geography": ["EU27"],
        "unit": "percent",
        "population": "enterprises",
        "value": 20,
        "strength": "strong",
    }
    if source_type == "eurostat":
        item["dataset_code"] = "isoc_eb_ai"
    return item


def market(market_id: str = "professional-services", score: int = 4) -> ScoredMarket:
    return ScoredMarket.model_validate(
        {
            "market_id": market_id,
            "name": "Professional services",
            "nace_codes": ["M69-M75"],
            "countries": ["DE", "AT"],
            "market_definition": "Mid-sized professional services firms.",
            "company_size_focus": ["50-249 employees"],
            "market_characteristics": ["Document-intensive"],
            "problems": ["Knowledge retrieval"],
            "applicable_services": ["RAG"],
            "entry_offer_hypothesis": "Knowledge retrieval pilot",
            "engagement_model": "Fixed-scope pilot",
            "buyer_role_hypotheses": ["COO"],
            "language_access": ["German", "English"],
            "risks": ["Confidential data"],
            "evidence": [evidence("quant", source_type="eurostat"), evidence("qual")],
            "score_components": [
                {
                    "dimension": dimension.value,
                    "score": score,
                    "rationale": "Evidence-backed rationale",
                    "evidence_ids": ["quant", "qual"],
                }
                for dimension in ScoreDimension
            ],
            "total_score": 1,
            "rank": 99,
            "confidence": 4,
            "assumptions": [],
            "unknowns": ["Procurement duration"],
            "next_research_questions": ["Which workflow is most costly?"],
        }
    )


def research_result(*markets: ScoredMarket) -> MarketResearchResult:
    return MarketResearchResult(
        research_date=date(2026, 8, 11),
        geographic_scope=["Europe"],
        methodology_summary="Eurostat and current primary-source research.",
        scorecard_version="model-supplied-value",
        markets=list(markets),
        limitations=[],
    )


def icp(market_id: str = "professional-services") -> ICPResult:
    return ICPResult.model_validate(
        {
            "market_id": market_id,
            "icp_id": "professional-services-mid-market",
            "title": "Mid-market professional services ICP",
            "profile_summary": "Document-intensive firms with an accountable operations buyer.",
            "firmographics": {
                "industries": ["Professional services"],
                "nace_codes": ["M69-M75"],
                "countries": ["DE", "AT"],
                "employee_range": "50-249",
                "turnover_range": None,
                "ownership_and_stage": ["Established private firm"],
                "operating_footprint": ["Multiple teams"],
                "regulated_data_exposure": ["Client-confidential documents"],
            },
            "operating_profile": {
                "target_workflows": ["Knowledge retrieval"],
                "work_volume_signals": ["Large document repository"],
                "languages": ["German", "English"],
                "available_data": ["Policies", "engagement documents"],
                "integration_environment": ["Cloud document platform"],
                "current_manual_effort": ["Repeated expert search"],
                "desired_outcomes": ["Faster reliable answers"],
            },
            "technographics": {
                "deployment_posture": ["Cloud or private cloud"],
                "systems_of_record": ["Document management system"],
                "knowledge_and_document_platforms": ["SharePoint-like platform"],
                "communication_stack": ["Business messaging"],
                "ai_maturity": "Experimenting",
                "security_constraints": ["Access control preservation"],
            },
            "pains_and_jobs": [
                {
                    "pain": "Slow knowledge retrieval",
                    "job_to_be_done": "Find an authoritative answer",
                    "business_impact": "Expert time is consumed",
                    "evidence_ids": ["quant", "qual"],
                }
            ],
            "service_fit": [
                {
                    "service": "RAG",
                    "use_case": "Permission-aware retrieval",
                    "fit_rationale": "Matches document workflow",
                    "required_customer_inputs": ["Representative documents"],
                }
            ],
            "buying_committee": [
                {
                    "role_type": "economic_buyer",
                    "likely_titles": ["COO"],
                    "priorities": ["Efficiency"],
                    "concerns": ["Risk"],
                }
            ],
            "purchase_triggers": [
                {
                    "signal": "Knowledge-platform migration",
                    "why_now": "Workflow is already changing",
                    "freshness_window": "6 months",
                    "discoverability": "Job posts and announcements",
                }
            ],
            "qualification_signals": [
                {
                    "signal": "Central document platform",
                    "fit_or_intent": "fit",
                    "verification_method": "Technical discovery",
                }
            ],
            "disqualifiers": [{"condition": "No digital corpus", "reason": "No usable input"}],
            "likely_objections": [{"objection": "Data leakage", "response_hypothesis": "Private deployment"}],
            "entry_offer": {
                "name": "Retrieval pilot",
                "scope": "One repository and one team",
                "expected_outcome": "Measured answer-quality baseline",
                "required_inputs": ["Documents", "evaluation questions"],
                "delivery_window": "2-4 weeks",
                "expansion_path": "Additional repositories",
            },
            "fit_score": {
                "score": 4,
                "rationale": "Strong workflow and service fit",
                "evidence_ids": ["quant", "qual"],
            },
            "intent_signal_model": {
                "high_intent_signals": ["Active AI procurement"],
                "medium_intent_signals": ["Relevant hiring"],
                "low_intent_state": "No current workflow change",
            },
            "evidence": [evidence("quant", source_type="eurostat"), evidence("qual")],
            "assumptions": ["Documents are digitized"],
            "unknowns": ["Exact platform"],
            "validation_questions": ["Who owns the workflow?"],
        }
    )


def test_market_schema_requires_every_score_dimension_once() -> None:
    payload = market().model_dump(mode="json")
    payload["score_components"] = payload["score_components"][:-1]

    with pytest.raises(ValidationError, match="every score dimension exactly once"):
        ScoredMarket.model_validate(payload)


def test_eurostat_evidence_requires_dataset_code() -> None:
    payload = market().model_dump(mode="json")
    payload["evidence"][0]["dataset_code"] = None

    with pytest.raises(ValidationError, match="requires dataset_code"):
        ScoredMarket.model_validate(payload)


def test_score_is_normalized_and_model_totals_are_overwritten() -> None:
    lower = market("alpha-market", score=3)
    higher = market("zulu-market", score=5)

    ranked = rank_markets(research_result(lower, higher))

    assert calculate_market_score(lower) == 60.0
    assert ranked.scorecard_version == SCORECARD_VERSION
    assert [(item.market_id, item.total_score, item.rank) for item in ranked.markets] == [
        ("zulu-market", 100.0, 1),
        ("alpha-market", 60.0, 2),
    ]


def test_tied_markets_use_stable_id_tie_breaker() -> None:
    ranked = rank_markets(research_result(market("zulu"), market("alpha")))
    assert [item.market_id for item in ranked.markets] == ["alpha", "zulu"]


def test_icp_rejects_undefined_evidence_references() -> None:
    payload = icp().model_dump(mode="json")
    payload["fit_score"]["evidence_ids"] = ["missing"]

    with pytest.raises(ValidationError, match="undefined evidence"):
        ICPResult.model_validate(payload)


def test_icp_markdown_is_deterministic_and_contains_sources() -> None:
    rendered = render_icp_markdown(icp())
    assert rendered.startswith("# Mid-market professional services ICP")
    assert "## Buying committee" in rendered
    assert "[Primary source](https://example.com/source)" in rendered


def test_icp_writer_refuses_overwrite(tmp_path: Path) -> None:
    paths = write_icp_result(icp(), tmp_path)
    assert all(path.exists() for path in paths)

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        write_icp_result(icp(), tmp_path)


def test_icp_schema_rejects_path_traversal() -> None:
    payload = icp().model_dump(mode="json")
    payload["market_id"] = "../escape"

    with pytest.raises(ValidationError):
        ICPResult.model_validate(payload)


def test_evidence_timestamp_is_timezone_aware() -> None:
    item = icp().evidence[0]
    assert item.retrieved_at == datetime(2026, 8, 11, 10, tzinfo=timezone.utc)
