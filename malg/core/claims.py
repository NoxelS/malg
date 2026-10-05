"""Host validation for model claim proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel

from malg.core.models.account import AccountResearchResult, AccountValidationOutcome
from malg.core.models.research import ClaimProposal, ResearchResult


@dataclass(frozen=True)
class ValidatedClaim:
    """A claim whose references and quote were checked by the host."""

    claim_id: str
    text: str
    excerpt_ids: tuple[str, ...]
    quote: str


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
        if result.outcome in {"complete", "partial"} and not claims:
            raise ClaimValidationError("missing_evidence_claim")
    except ClaimValidationError as error:
        updates: dict[str, object] = {
            "outcome": "insufficient_evidence",
            "data": None,
            "observations": [],
            "unknowns": [error.code],
        }
        if isinstance(result, AccountResearchResult):
            updates.update(identity=None, qualification=AccountValidationOutcome.NEEDS_REVIEW)
        return result.model_copy(update=updates), []
    return result, claims
