"""Shared canonical fixtures for persistence API tests."""

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from tests.test_campaign_research_agent import campaign_payload

from malg.api.app import create_app
from malg.config import AuthConfig
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPResult


def authenticated_client(engine: Engine) -> TestClient:
    """Create an API client with the deterministic test administrator token."""
    client = TestClient(
        create_app(database_engine=engine, auth_config=AuthConfig("admin", "test-password"))
    )
    token = client.post(
        "/api/v1/auth/token", json={"username": "admin", "password": "test-password"}
    ).json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


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
