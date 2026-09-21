"""Host validation for model claim proposals."""

from __future__ import annotations

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


def validate_claim_proposal(proposal: ClaimProposal, excerpts: dict[str, str]) -> ValidatedClaim:
    """Reject foreign excerpt IDs and quotes not present in supplied excerpts."""
    if any(identifier not in excerpts for identifier in proposal.excerpt_ids):
        raise ValueError("claim references an excerpt outside the supplied context")
    if not any(proposal.quote in excerpts[identifier] for identifier in proposal.excerpt_ids):
        raise ValueError("claim quote is not an exact substring of a supplied excerpt")
    return ValidatedClaim(str(uuid4()), proposal.text, tuple(proposal.excerpt_ids), proposal.quote)
