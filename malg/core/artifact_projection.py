"""Read-only projections from historical rich artifacts into v2 envelopes."""

from __future__ import annotations

from typing import Any


def project_campaign(payload: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    """Project legacy campaign fields without inventing evidence."""
    return {"campaign_id": campaign_id, "schema_version": 2, "origin": "legacy_projection",
            "title": payload.get("title", ""), "geographies": payload.get("geographies", []),
            "industries": payload.get("industries", []), "company_size_focus": payload.get("company_size_focus", []),
            "target_workflows": payload.get("target_workflows", []), "problem_statement": payload.get("problem_statement", ""),
            "offering": payload.get("offering", payload.get("positioning", "")), "exclusions": payload.get("exclusions", []),
            "assumptions": payload.get("assumptions", []), "unknowns": ["historical evidence was not revalidated"], "claim_ids": []}


def project_icp(payload: dict[str, Any], campaign_id: str, icp_id: str) -> dict[str, Any]:
    """Project legacy ICP targeting criteria as historical, not verified evidence."""
    identity = payload.get("identity", {})
    axes = ("industry", "geography", "company_size_band", "primary_workflow", "primary_buyer_role", "deployment_posture")
    required = [{"criterion_id": axis, "description": identity.get(axis, ""), "verification_hint": "historical targeting criterion"} for axis in axes]
    return {"campaign_id": campaign_id, "icp_id": icp_id, "schema_version": 2, "title": payload.get("title", ""),
            "identity": identity, "required_attributes": required, "preferred_attributes": [],
            "disqualifiers": payload.get("disqualifiers", []), "observable_signals": [], "buying_roles": [],
            "search_queries": [], "claim_ids": [], "assumptions": ["legacy targeting fields"],
            "unknowns": ["historical evidence was not revalidated"]}
