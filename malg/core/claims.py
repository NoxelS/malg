"""Host validation for model claim proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from malg.core.models.research import ClaimProposal


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
    if not any(
        quote in _normalized_whitespace(excerpts[identifier]) for identifier in proposal.excerpt_ids
    ):
        raise ClaimValidationError("invalid_evidence_quote")
    return ValidatedClaim(str(uuid4()), proposal.text, tuple(proposal.excerpt_ids), proposal.quote)
