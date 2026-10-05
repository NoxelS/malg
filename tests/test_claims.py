"""Host validation of generated evidence proposals."""

import pytest

from malg.core.claims import (
    ClaimValidationError,
    validate_claim_proposal,
    validate_research_evidence,
)
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
        (proposal(quote=" "), {"excerpt-1": "Exact source quote"}, "invalid_evidence_quote"),
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


@pytest.mark.parametrize("invalid", ["quote", "reference", "missing"])
def test_unpublishable_account_discards_data_identity_and_all_claims(invalid) -> None:
    """Even one invalid observation cannot leave publishable account data behind."""
    from malg.core.models.account import (
        AccountData,
        AccountEngagementSignal,
        AccountIdentity,
        AccountResearchResult,
    )
    from malg.core.models.research import FieldObservation

    observations = [
        FieldObservation(
            field="name",
            text="Supported name",
            quote="Exact source quote",
            excerpt_ids=["excerpt-1"],
        )
    ]
    if invalid == "missing":
        observations = []
    else:
        observations.append(
            FieldObservation(
                field="name",
                text="Unsupported name",
                quote="Altered quote" if invalid == "quote" else "Exact source quote",
                excerpt_ids=["invented" if invalid == "reference" else "excerpt-1"],
            )
        )
    original = AccountResearchResult(
        outcome="complete",
        data=AccountData(name="Observed company"),
        identity=AccountIdentity(
            display_name="Observed company", official_website="https://example.com"
        ),
        engagement_signal=AccountEngagementSignal(
            signal_type="freelance_initiative_application",
            title="Freelance Initiativbewerbung",
            source_url="https://example.com/freelance",
            invited_work="Freelance software delivery support.",
            response_route="Use the freelance application form.",
            status="open",
        ),
        signal_observations=[
            FieldObservation(
                field="response_route",
                text="A freelance application route is available.",
                quote="Exact source quote",
                excerpt_ids=["excerpt-1"],
            )
        ],
        qualification="accepted",
        observations=observations,
    )
    result, claims = validate_research_evidence(original, {"excerpt-1": "Exact source quote"})
    assert result.outcome == "insufficient_evidence"
    assert result.data is None
    assert result.identity is None
    assert result.qualification == "needs_review"
    assert result.engagement_signal is None
    assert not result.observations
    assert not result.signal_observations
    assert not claims
    assert result.unknowns == [
        {
            "quote": "invalid_evidence_quote",
            "reference": "invalid_evidence_reference",
            "missing": "missing_evidence_claim",
        }[invalid]
    ]
    assert original.data is not None


def test_valid_research_retains_publishable_data_and_claims() -> None:
    from malg.core.models.campaign import CampaignData
    from malg.core.models.research import FieldObservation, ResearchResult

    original = ResearchResult[CampaignData](
        outcome="complete",
        data=CampaignData(name="Observed", objective="Research"),
        observations=[
            FieldObservation(
                field="name",
                text="Observed name",
                quote="Exact source quote",
                excerpt_ids=["excerpt-1"],
            )
        ],
    )
    result, claims = validate_research_evidence(original, {"excerpt-1": "Exact source quote"})
    assert result == original
    assert len(claims) == 1
