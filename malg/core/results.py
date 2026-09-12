"""Safe serialization and deterministic Markdown rendering for MALG results."""

from __future__ import annotations

from pydantic import BaseModel

from malg.core.models.account import AccountProfile
from malg.core.models.icp import ICPResult


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- None identified"


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}\n"


def _mapping_bullets(value: BaseModel) -> str:
    rows: list[str] = []
    for key, item in value.model_dump(mode="json").items():
        label = key.replace("_", " ").title()
        if isinstance(item, list):
            rendered = "; ".join(str(entry) for entry in item) or "Unknown"
        else:
            rendered = str(item) if item is not None else "Unknown"
        rows.append(f"- **{label}:** {rendered}")
    return "\n".join(rows)


def render_icp_markdown(icp: ICPResult) -> str:
    """Render a stable human-readable view of the canonical ICP object."""
    sections = [
        f"# {icp.title}\n\n**Campaign ID:** `{icp.campaign_id}`  \n**ICP ID:** `{icp.icp_id}`\n",
        _section("Profile summary", icp.profile_summary),
        _section("Firmographics", _mapping_bullets(icp.firmographics)),
        _section("Operating profile", _mapping_bullets(icp.operating_profile)),
        _section("Technographics", _mapping_bullets(icp.technographics)),
        _section(
            "Pains and jobs to be done",
            "\n".join(
                f"- **{item.pain}:** {item.job_to_be_done} — {item.business_impact} (evidence: {', '.join(item.evidence_ids)})"
                for item in icp.pains_and_jobs
            ),
        ),
        _section(
            "Service fit",
            "\n".join(
                f"- **{item.service}:** {item.use_case} — {item.fit_rationale}"
                for item in icp.service_fit
            ),
        ),
        _section(
            "Buying committee",
            "\n".join(
                f"- **{item.role_type.value}:** {', '.join(item.likely_titles)}; "
                f"priorities: {', '.join(item.priorities)}; concerns: {', '.join(item.concerns)}"
                for item in icp.buying_committee
            ),
        ),
        _section(
            "Purchase triggers",
            "\n".join(
                f"- **{item.signal}:** {item.why_now} (window: {item.freshness_window}; discovery: {item.discoverability})"
                for item in icp.purchase_triggers
            ),
        ),
        _section(
            "Qualification signals",
            "\n".join(
                f"- **{item.fit_or_intent}:** {item.signal} — verify via {item.verification_method}"
                for item in icp.qualification_signals
            ),
        ),
        _section(
            "Disqualifiers",
            "\n".join(f"- **{item.condition}:** {item.reason}" for item in icp.disqualifiers)
            or "- None identified",
        ),
        _section(
            "Likely objections",
            "\n".join(
                f"- **{item.objection}:** {item.response_hypothesis}"
                for item in icp.likely_objections
            )
            or "- None identified",
        ),
        _section("Entry offer", _mapping_bullets(icp.entry_offer)),
        _section(
            "Fit and intent",
            f"**Fit score:** {icp.fit_score.score}/5 — {icp.fit_score.rationale}\n\n"
            + _mapping_bullets(icp.intent_signal_model),
        ),
        _section("Assumptions", _bullets(icp.assumptions)),
        _section("Unknowns", _bullets(icp.unknowns)),
        _section("Validation questions", _bullets(icp.validation_questions)),
        _section(
            "Evidence",
            "\n".join(
                f"- **{item.evidence_id}:** [{item.source_title}]({item.source_url}) — "
                f"{item.claim} (retrieved {item.retrieved_at.date().isoformat()})"
                for item in icp.evidence
            ),
        ),
    ]
    return "\n".join(sections).rstrip() + "\n"


def render_account_profile_markdown(profile: AccountProfile) -> str:
    """Render a stable human-readable view of one unpersisted account candidate."""
    sections = [
        f"# Account candidate: {profile.identity.display_name}\n\n"
        f"**Campaign ID:** `{profile.campaign_id}`  \n"
        f"**ICP ID:** `{profile.icp_id}`\n",
        _section("Identity", _mapping_bullets(profile.identity)),
        _section("Firmographics", _mapping_bullets(profile.firmographics)),
        _section("Operating profile", _mapping_bullets(profile.operating_profile)),
        _section(
            "ICP fit",
            f"**Score:** {profile.fit.score}/5 — {profile.fit.rationale}\n\n"
            + _section("Matched attributes", _bullets(profile.fit.matched_attributes)),
        ),
        _section(
            "Account entrypoints",
            "\n".join(
                f"- **{endpoint.kind.value}:** {endpoint.value} ({endpoint.discovery_method.value})"
                for endpoint in profile.account_endpoints
            )
            or "- None found",
        ),
        _section(
            "Contacts",
            "\n".join(
                f"- **{contact.full_name}:** {contact.title}; "
                f"entrypoints: {', '.join(endpoint.kind.value for endpoint in contact.endpoints) or 'none'}"
                for contact in profile.contacts
            )
            or "- None found",
        ),
        _section(
            "Evidence",
            "\n".join(
                f"- **{item.evidence_id}:** [{item.source_title}]({item.source_url}) — "
                f"{item.claim} (retrieved {item.retrieved_at.date().isoformat()})"
                for item in profile.evidence
            ),
        ),
        _section("Assumptions", _bullets(profile.assumptions)),
        _section("Unknowns", _bullets(profile.unknowns)),
    ]
    return "\n".join(sections).rstrip() + "\n"
