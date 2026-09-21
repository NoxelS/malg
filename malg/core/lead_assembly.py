"""Host-owned lead completeness and human-review assembly."""

from __future__ import annotations

from typing import Any


def assemble_lead(*, workflow_id: str, qualified: bool, project: dict[str, Any] | None, contact: dict[str, Any] | None) -> dict[str, Any]:
    """Return a deterministic partial lead without inferring missing evidence."""
    has_project = bool(project and project.get("outcome") == "complete")
    has_contact = bool(contact and contact.get("outcome") == "complete" and contact.get("current"))
    if not qualified:
        completeness = "unqualified"
    elif has_project and has_contact:
        completeness = "complete"
    elif not has_project and not has_contact:
        completeness = "missing_project_and_contact"
    elif not has_project:
        completeness = "missing_project"
    else:
        completeness = "missing_named_contact"
    limitations = []
    if not has_project:
        limitations.append("company-specific project evidence unavailable")
    if not has_contact:
        limitations.append("current relevant named contact unavailable")
    return {"workflow_id": workflow_id, "completeness": completeness, "review_status": "pending", "limitations": limitations, "project": project, "contact": contact}
