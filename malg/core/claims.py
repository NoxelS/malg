"""Host validation for model claim proposals."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from pydantic import BaseModel

from malg.core.models.account import AccountResearchResult, AccountValidationOutcome
from malg.core.models.research import (
    ClaimProposal,
    EvidenceCorrections,
    EvidenceIssue,
    EvidenceRepairRequest,
    ResearchResult,
)


@dataclass(frozen=True)
class ValidatedClaim:
    """A claim whose references and quote were checked by the host."""

    claim_id: str
    text: str
    excerpt_ids: tuple[str, ...]
    quote: str


class EvidenceRepairUnavailable(RuntimeError):
    """The bounded reasoning adapter could not produce a valid citation repair."""


class ClaimValidationError(ValueError):
    """A safe, durable reason why a generated evidence reference was rejected."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _normalized_whitespace(value: str) -> str:
    """Collapse formatting-only differences without changing quoted words or punctuation."""
    return re.sub(r"\s+", " ", value).strip()


def validate_claim_proposal(proposal: ClaimProposal, excerpts: dict[str, str]) -> ValidatedClaim:
    """Reject foreign evidence references while tolerating whitespace-only quote formatting."""
    if any(identifier not in excerpts for identifier in proposal.excerpt_ids):
        raise ClaimValidationError("invalid_evidence_reference")
    quote = _normalized_whitespace(proposal.quote)
    if not quote or not any(
        quote in _normalized_whitespace(excerpts[identifier]) for identifier in proposal.excerpt_ids
    ):
        raise ClaimValidationError("invalid_evidence_quote")
    return ValidatedClaim(str(uuid4()), proposal.text, tuple(proposal.excerpt_ids), proposal.quote)


def validate_research_evidence[T: BaseModel](
    result: ResearchResult[T], excerpts: dict[str, str]
) -> tuple[ResearchResult[T], list[ValidatedClaim]]:
    """Validate all observations or discard unpublishable data as insufficient evidence.

    Invalid quotes, foreign references and missing claims discard the entire
    candidate, never weaken citation checks or retain partially validated claims.
    Account identity and qualification are cleared to prevent downstream publication.
    The original result is not mutated; safe reason codes remain in unknowns.
    """
    if result.data is None:
        return result, []
    try:
        claims = [validate_claim_proposal(item, excerpts) for item in result.observations]
        if result.outcome in {"complete", "partial"} and not result.observations:
            raise ClaimValidationError("missing_evidence_claim")
        if isinstance(result, AccountResearchResult):
            if result.engagement_signal is not None and not result.signal_observations:
                raise ClaimValidationError("missing_evidence_claim")
            claims.extend(
                validate_claim_proposal(item, excerpts) for item in result.signal_observations
            )
    except ClaimValidationError as error:
        updates: dict[str, object] = {
            "outcome": "insufficient_evidence",
            "data": None,
            "observations": [],
            "unknowns": [error.code],
            "reason_code": error.code,
        }
        if isinstance(result, AccountResearchResult):
            updates.update(
                identity=None,
                qualification=AccountValidationOutcome.NEEDS_REVIEW,
                engagement_signal=None,
                signal_observations=[],
            )
        return result.model_copy(update=updates), []
    return result, claims


async def validate_account_evidence(
    result: AccountResearchResult,
    excerpts: dict[str, str],
    repair: Callable[[EvidenceRepairRequest], Awaitable[EvidenceCorrections]],
    *,
    recorder: Callable[[str, object], None] | None = None,
) -> tuple[AccountResearchResult, list[ValidatedClaim]]:
    """Validate an account and attempt one citation-only repair before rejecting it.

    Repair receives host excerpts as untrusted data and can replace citations only
    at rejected observation positions. Identity, qualification, claim text and fields
    remain unchanged. Every resulting observation is revalidated. Missing claims or
    missing source text cannot be repaired. Adapter failures, cancellation and real
    budget exhaustion propagate to the supervised stage. A bounded repair that
    cannot complete leaves the original candidate unpublished.
    """
    validated, claims = validate_research_evidence(result, excerpts)
    if (
        result.data is None
        or validated.data is not None
        or not excerpts
        or not result.observations
        or (result.engagement_signal is not None and not result.signal_observations)
    ):
        return cast(AccountResearchResult, validated), claims
    issues: list[EvidenceIssue] = []
    for group in ("observations", "signal_observations"):
        for index, observation in enumerate(getattr(result, group)):
            try:
                validate_claim_proposal(observation, excerpts)
            except ClaimValidationError as error:
                issues.append(
                    EvidenceIssue.model_validate(
                        {
                            "group": group,
                            "index": index,
                            "observation": observation,
                            "reason_code": error.code,
                        }
                    )
                )
    if not issues:
        return cast(AccountResearchResult, validated), claims
    if recorder:
        recorder("evidence_repair_started", {"issues": [item.model_dump() for item in issues]})
    try:
        repaired = await repair(EvidenceRepairRequest(issues=issues, excerpts=excerpts))
    except EvidenceRepairUnavailable:
        if recorder:
            recorder("evidence_repair_rejected", {"reason": "evidence_repair_unavailable"})
        return cast(AccountResearchResult, validated), claims
    positions = {(issue.group, issue.index) for issue in issues}
    corrections = repaired.corrections
    supplied = {(item.group, item.index) for item in corrections}
    if len(supplied) != len(corrections) or supplied != positions:
        if recorder:
            recorder("evidence_repair_rejected", {"reason": "invalid_repair_positions"})
        return cast(AccountResearchResult, validated), claims
    observations = list(result.observations)
    signal_observations = list(result.signal_observations)
    for correction in corrections:
        target = observations if correction.group == "observations" else signal_observations
        target[correction.index] = target[correction.index].model_copy(
            update={
                "excerpt_ids": correction.excerpt_ids,
                "quote": correction.quote,
            }
        )
    candidate = result.model_copy(
        update={
            "observations": observations,
            "signal_observations": signal_observations,
        }
    )
    final, claims = validate_research_evidence(candidate, excerpts)
    if recorder:
        recorder(
            "evidence_repair_completed",
            {"accepted": final.data is not None, "reason": final.reason_code},
        )
    return cast(AccountResearchResult, final), claims
