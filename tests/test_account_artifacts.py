"""Observable persistence and API outcomes for account research artifacts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from tests.fixtures import _icp, authenticated_client
from tests.test_campaign_research_agent import campaign_payload

from malg.core.models.account import AccountCandidate, CommunicationEndpointCandidate
from malg.database import Base


def _engine() -> Engine:
    """Create an isolated in-memory database for account API tests."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


def account_candidate_payload() -> dict[str, object]:
    """Return a compact sourced account candidate used across deterministic tests."""
    observed_at = datetime(2026, 9, 12, tzinfo=UTC).isoformat()
    return {
        "campaign_id": "dach-private-ai-critical-workflows",
        "icp_id": "manufacturing-ops",
        "identity": {
            "display_name": "Example Manufacturing GmbH",
            "legal_name": "Example Manufacturing GmbH",
            "registry_jurisdiction": "DE",
            "registration_number": "HRB 12345",
            "operational_status": "active",
            "headquarters": "Berlin, Germany",
            "official_website": "https://example-manufacturing.test/",
            "official_domains": ["example-manufacturing.test"],
        },
        "firmographics": {
            "industries": ["Industrial manufacturing"],
            "nace_codes": ["C25"],
            "employee_range": "250-499",
            "operating_regions": ["Germany"],
        },
        "operating_profile": {
            "key_workflows": ["Incident intake"],
            "current_tools": ["ERP"],
            "deployment_posture": ["Private cloud"],
            "security_requirements": ["Data residency"],
        },
        "fit": {
            "score": 4,
            "rationale": "The public manufacturing and workflow evidence matches the ICP.",
            "matched_attributes": ["DACH manufacturing", "Incident intake"],
            "evidence_ids": ["company-site"],
        },
        "account_endpoints": [
            {
                "kind": "email",
                "value": "info@example-manufacturing.test",
                "discovery_method": "published",
                "source_url": "https://example-manufacturing.test/contact",
                "source_title": "Example Manufacturing contact",
                "observed_at": observed_at,
            }
        ],
        "contacts": [
            {
                "full_name": "Ada Example",
                "title": "Chief Technology Officer",
                "buyer_role": "technical_approver",
                "employment_source_url": "https://example-manufacturing.test/team",
                "employment_source_title": "Example Manufacturing leadership",
                "observed_at": observed_at,
                "endpoints": [
                    {
                        "kind": "linkedin",
                        "value": "https://www.linkedin.com/in/ada-example",
                        "discovery_method": "published",
                        "source_url": "https://example-manufacturing.test/team",
                        "source_title": "Example Manufacturing leadership",
                        "observed_at": observed_at,
                    }
                ],
            }
        ],
        "evidence": [
            {
                "evidence_id": "company-site",
                "claim": "Example Manufacturing operates industrial manufacturing workflows in Germany.",
                "source_type": "company",
                "evidence_kind": "qualitative",
                "source_title": "Example Manufacturing",
                "source_url": "https://example-manufacturing.test/",
                "retrieved_at": observed_at,
                "geography": ["Germany"],
                "strength": "strong",
            },
            {
                "evidence_id": "team-page",
                "claim": "Ada Example is listed as Chief Technology Officer.",
                "source_type": "company",
                "evidence_kind": "qualitative",
                "source_title": "Example Manufacturing leadership",
                "source_url": "https://example-manufacturing.test/team",
                "retrieved_at": observed_at,
                "geography": ["Germany"],
                "strength": "strong",
            },
        ],
    }


def test_account_candidate_requires_resolved_fit_evidence_and_observed_linkedin() -> None:
    payload = account_candidate_payload()
    candidate = AccountCandidate.model_validate(payload)
    assert candidate.fit.evidence_ids == ["company-site"]

    endpoint = payload["contacts"][0]["endpoints"][0]  # type: ignore[index]
    endpoint["discovery_method"] = "inferred"  # type: ignore[index]
    with pytest.raises(ValidationError, match="LinkedIn endpoints"):
        AccountCandidate.model_validate(payload)

    with pytest.raises(ValidationError, match="LinkedIn endpoints"):
        CommunicationEndpointCandidate.model_validate(endpoint)


def test_account_api_persists_global_identity_campaign_match_contacts_and_validation() -> None:
    client = authenticated_client(_engine())
    campaign = campaign_payload()
    icp = _icp("manufacturing-ops", "Incident intake").model_dump(mode="json")
    candidate = account_candidate_payload()
    assert client.post("/api/v1/campaigns", json=campaign).status_code == 201
    assert (
        client.post(f"/api/v1/campaigns/{campaign['campaign_id']}/icps", json=icp).status_code
        == 201
    )

    created = client.post(
        f"/api/v1/campaigns/{campaign['campaign_id']}/icps/{icp['icp_id']}/accounts",
        json=candidate,
    )
    assert created.status_code == 201
    match = created.json()
    account_id = match["account_id"]
    assert match["candidate"]["identity"]["display_name"] == "Example Manufacturing GmbH"
    assert match["validation"] is None

    assert client.get("/api/v1/accounts").json()[0]["account_id"] == account_id
    assert (
        client.get(f"/api/v1/accounts/{account_id}/contacts").json()[0]["full_name"]
        == "Ada Example"
    )
    assert client.get(f"/api/v1/accounts/{account_id}/entrypoints").json()[0]["kind"] == "email"

    assessment = {
        "outcome": "accepted",
        "rationale": "The legal identity, website, and ICP fit are corroborated.",
        "checks": [
            {
                "check_type": "identity",
                "target": "Example Manufacturing GmbH",
                "outcome": "pass",
                "reason": "Official website and registry identity agree.",
            }
        ],
        "validated_at": datetime(2026, 9, 12, tzinfo=UTC).isoformat(),
    }
    validated = client.post(
        f"/api/v1/campaigns/{campaign['campaign_id']}/icps/{icp['icp_id']}/accounts/{account_id}/validations",
        json=assessment,
    )
    assert validated.status_code == 200
    assert validated.json()["validation"]["outcome"] == "accepted"

    listed = client.get(
        f"/api/v1/campaigns/{campaign['campaign_id']}/icps/{icp['icp_id']}/accounts"
    )
    assert listed.status_code == 200
    assert listed.json()[0]["account_id"] == account_id
