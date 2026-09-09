"""Safe serialization and deterministic Markdown rendering for MALG results."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel

from malg.core.models.account import AccountProfile
from malg.core.models.icp import ICPResult

SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def _validate_id(value: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise ValueError(f"Unsafe result identifier: {value!r}")
    return value


def _atomic_write(path: Path, content: str, *, overwrite: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing result: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return path


def write_json_result(value: BaseModel, path: Path, *, overwrite: bool = False) -> Path:
    payload = value.model_dump(mode="json")
    return _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n", overwrite=overwrite)


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
            "\n".join(f"- **{item.service}:** {item.use_case} — {item.fit_rationale}" for item in icp.service_fit),
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
            "\n".join(f"- **{item.fit_or_intent}:** {item.signal} — verify via {item.verification_method}" for item in icp.qualification_signals),
        ),
        _section(
            "Disqualifiers",
            "\n".join(f"- **{item.condition}:** {item.reason}" for item in icp.disqualifiers) or "- None identified",
        ),
        _section(
            "Likely objections",
            "\n".join(f"- **{item.objection}:** {item.response_hypothesis}" for item in icp.likely_objections) or "- None identified",
        ),
        _section("Entry offer", _mapping_bullets(icp.entry_offer)),
        _section(
            "Fit and intent",
            f"**Fit score:** {icp.fit_score.score}/5 — {icp.fit_score.rationale}\n\n" + _mapping_bullets(icp.intent_signal_model),
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


def write_icp_result(icp: ICPResult, output_root: Path, *, overwrite: bool = False) -> tuple[Path, Path]:
    """Persist canonical JSON and its Markdown rendering using trusted identifiers."""
    campaign_id = _validate_id(icp.campaign_id)
    markdown_path = output_root / "ICP" / f"{campaign_id}.md"
    json_path = output_root / "ICP" / f"{campaign_id}.json"
    if not overwrite and (markdown_path.exists() or json_path.exists()):
        raise FileExistsError(f"Refusing to overwrite existing ICP for {campaign_id}.")

    # JSON is canonical; write it first. A later Markdown failure remains recoverable.
    write_json_result(icp, json_path, overwrite=overwrite)
    _atomic_write(markdown_path, render_icp_markdown(icp), overwrite=overwrite)
    return json_path, markdown_path


def render_account_profile_markdown(profile: AccountProfile) -> str:
    """Render a stable human-readable view of one account profile."""
    sections = [
        f"# Account: {profile.firmographics.company_name}\n\n"
        f"**Account ID:** `{profile.account_id}`  \n"
        f"**ICP ID:** `{profile.icp_id}`  \n"
        f"**Region:** `{profile.region}`\n",
        _section("Firmographics", _mapping_bullets(profile.firmographics)),
        _section("Operating profile", _mapping_bullets(profile.operating_profile)),
        _section("Technographics", _mapping_bullets(profile.technographics)),
        _section(
            "Pains and jobs to be done",
            "\n".join(
                f"- **{item.pain}:** {item.job_to_be_done} — {item.business_impact} (evidence: {', '.join(item.evidence_ids)})"
                for item in profile.pains_and_jobs
            ),
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
        _section("Validation questions", _bullets(profile.validation_questions)),
    ]
    return "\n".join(sections).rstrip() + "\n"


def write_account_profile_result(profile: AccountProfile, output_root: Path, *, overwrite: bool = False) -> tuple[Path, Path]:
    """Persist canonical JSON and its Markdown rendering for one account profile."""
    icp_id = _validate_id(profile.icp_id)
    safe_region = re.sub(r"[^a-z0-9\-]", "-", profile.region.lower()).strip("-")
    base_dir = output_root / "accounts" / icp_id / safe_region
    json_path = base_dir / f"account_{profile.account_id}.json"
    md_path = base_dir / f"account_{profile.account_id}.md"
    if not overwrite and (json_path.exists() or md_path.exists()):
        raise FileExistsError(f"Refusing to overwrite existing account profile for {profile.account_id}.")

    write_json_result(profile, json_path, overwrite=overwrite)
    _atomic_write(md_path, render_account_profile_markdown(profile), overwrite=overwrite)
    return json_path, md_path
