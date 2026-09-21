"""Host orchestration for bounded qualification, project, contact, and review stages."""

from __future__ import annotations

from typing import Any

from malg.core.lead_assembly import assemble_lead


class ResearchStageRunner:
    """Compose independently persisted stage assessments without accepting model authority."""

    def qualification(self, *, identity_verified: bool, required_criteria: list[dict[str, Any]]) -> dict[str, Any]:
        """Assess required criteria; unknowns never become a qualification."""
        if not identity_verified:
            return {"outcome": "insufficient_evidence", "fit": "insufficient_evidence", "unknowns": ["identity"]}
        if any(item.get("outcome") == "not_met" for item in required_criteria):
            return {"outcome": "complete", "fit": "mismatch", "unknowns": []}
        if any(item.get("outcome") == "unknown" for item in required_criteria):
            return {"outcome": "insufficient_evidence", "fit": "insufficient_evidence", "unknowns": ["required criterion"]}
        return {"outcome": "complete", "fit": "qualified", "unknowns": []}

    def project(self, *, qualification: dict[str, Any], observation_claim_ids: list[str], hypothesis: str) -> dict[str, Any]:
        """Create one bounded project hypothesis only from a company-specific observation."""
        if qualification.get("fit") != "qualified" or not observation_claim_ids:
            return {"outcome": "insufficient_evidence", "unknowns": ["company-specific workflow observation"]}
        return {"outcome": "complete", "claim_ids": observation_claim_ids, "hypothesis": hypothesis, "confidence": "low"}

    def contact(self, *, employment_claim_ids: list[str], current: bool, relevant: bool) -> dict[str, Any]:
        """Accept a named person only with sourced current employment and relevance."""
        if not employment_claim_ids or not current or not relevant:
            return {"outcome": "insufficient_evidence", "current": False, "unknowns": ["current relevant named contact"]}
        return {"outcome": "complete", "current": True}

    def lead(self, *, workflow_id: str, qualification: dict[str, Any], project: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]:
        """Assemble a partial lead after either branch commits."""
        return assemble_lead(workflow_id=workflow_id, qualified=qualification.get("fit") == "qualified", project=project, contact=contact)
