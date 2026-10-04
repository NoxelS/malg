"""Host validation of generated evidence proposals."""

import pytest

from malg.core.claims import ClaimValidationError, validate_claim_proposal
from malg.core.models.research import ClaimProposal


def proposal(*, excerpt_id: str = "excerpt-1", quote: str = "Exact source quote") -> ClaimProposal:
    """Build one bounded evidence proposal for host-validation tests."""
    return ClaimProposal(text="Supported claim", excerpt_ids=[excerpt_id], quote=quote)


def test_claim_validation_accepts_whitespace_only_quote_formatting() -> None:
    """Line wrapping in generated output does not invalidate otherwise exact evidence."""
    claim = validate_claim_proposal(
        proposal(quote="Exact\n source\tquote"), {"excerpt-1": "Before Exact source quote after"}
    )

    assert claim.excerpt_ids == ("excerpt-1",)


@pytest.mark.parametrize(
    ("claim", "excerpts", "code"),
    [
        (
            proposal(excerpt_id="invented"),
            {"excerpt-1": "Exact source quote"},
            "invalid_evidence_reference",
        ),
        (
            proposal(quote="altered source quote"),
            {"excerpt-1": "Exact source quote"},
            "invalid_evidence_quote",
        ),
    ],
)
def test_claim_validation_exposes_safe_invalid_evidence_codes(
    claim: ClaimProposal, excerpts: dict[str, str], code: str
) -> None:
    """Foreign references and altered words remain fail-closed with stable operator codes."""
    with pytest.raises(ClaimValidationError, match=f"^{code}$") as error:
        validate_claim_proposal(claim, excerpts)

    assert error.value.code == code
