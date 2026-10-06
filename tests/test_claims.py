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


@pytest.mark.parametrize("qualification", ["accepted", "needs_review"])
def test_account_citation_repair_preserves_business_data_and_qualification(qualification) -> None:
    """Repairing a wrong excerpt association can retain a sourced candidate without upgrading it."""
    import asyncio

    from malg.core.claims import validate_account_evidence
    from malg.core.models.account import AccountData, AccountIdentity, AccountResearchResult
    from malg.core.models.research import EvidenceCorrections, FieldObservation

    original = AccountResearchResult(
        outcome="complete" if qualification == "accepted" else "needs_review",
        qualification=qualification,
        data=AccountData(name="Observed company"),
        identity=AccountIdentity(
            display_name="Observed company", official_website="https://example.com"
        ),
        observations=[
            FieldObservation(
                field="name",
                text="The company is named Observed company",
                quote="Observed company",
                excerpt_ids=["wrong-excerpt"],
            )
        ],
    )
    calls = []

    async def repair(request):
        calls.append(request)
        return EvidenceCorrections.model_validate(
            {
                "corrections": [
                    {
                        "group": "observations",
                        "index": 0,
                        "excerpt_ids": ["correct-excerpt"],
                        "quote": "Observed company",
                    }
                ]
            }
        )

    result, claims = asyncio.run(
        validate_account_evidence(
            original,
            {
                "wrong-excerpt": "Software delivery",
                "correct-excerpt": "Observed company welcomes freelancers",
            },
            repair,
        )
    )
    assert result.data == original.data
    assert result.identity == original.identity
    assert result.qualification == original.qualification
    assert result.outcome == original.outcome
    assert result.observations[0].text == original.observations[0].text
    assert result.observations[0].excerpt_ids == ["correct-excerpt"]
    assert original.observations[0].excerpt_ids == ["wrong-excerpt"]
    assert len(claims) == 1
    assert len(calls) == 1
    assert calls[0].issues[0].reason_code == "invalid_evidence_quote"


@pytest.mark.parametrize(
    "failure", ["altered_quote", "foreign_id", "new_position", "duplicate", "empty"]
)
def test_account_repair_remains_fail_closed(failure) -> None:
    """Invalid repair responses cannot add claims or retain publishable company data."""
    import asyncio

    from malg.core.claims import validate_account_evidence
    from malg.core.models.account import AccountData, AccountIdentity, AccountResearchResult
    from malg.core.models.research import EvidenceCorrections, FieldObservation

    result = AccountResearchResult(
        outcome="complete",
        qualification="accepted",
        data=AccountData(name="Company"),
        identity=AccountIdentity(display_name="Company", official_website="https://example.com"),
        observations=[
            FieldObservation(
                field="name",
                text="Observed name",
                quote="Incorrect name",
                excerpt_ids=["excerpt-1"],
            )
        ],
    )

    async def repair(request):
        correction = {
            "group": "observations",
            "index": 0,
            "excerpt_ids": ["excerpt-1"],
            "quote": "Company",
        }
        if failure == "altered_quote":
            correction["quote"] = "Paraphrased Company"
        elif failure == "foreign_id":
            correction["excerpt_ids"] = ["invented"]
        elif failure == "new_position":
            correction["index"] = 1
        corrections = [] if failure == "empty" else [correction]
        if failure == "duplicate":
            corrections.append(correction)
        return EvidenceCorrections.model_validate({"corrections": corrections})

    rejected, claims = asyncio.run(
        validate_account_evidence(result, {"excerpt-1": "Company"}, repair)
    )
    assert rejected.outcome == "insufficient_evidence"
    assert rejected.data is None
    assert rejected.identity is None
    assert rejected.qualification == "needs_review"
    assert not claims


def test_missing_evidence_does_not_trigger_generation() -> None:
    """No repair is attempted when there are no claims or no fetched source text."""
    import asyncio

    from malg.core.claims import validate_account_evidence
    from malg.core.models.account import AccountData, AccountIdentity, AccountResearchResult

    original = AccountResearchResult(
        outcome="complete",
        qualification="accepted",
        data=AccountData(name="Company"),
        identity=AccountIdentity(display_name="Company", official_website="https://example.com"),
    )

    async def unexpected_repair(request):
        raise AssertionError("Missing evidence is not repairable")

    for excerpts in ({}, {"excerpt-1": "Company"}):
        result, claims = asyncio.run(
            validate_account_evidence(original, excerpts, unexpected_repair)
        )
        assert result.reason_code == "missing_evidence_claim"
        assert result.data is None
        assert not claims


def test_account_repair_propagates_real_budget_denial() -> None:
    """Citation repair shares the stage budget instead of starting an unbounded new run."""
    import asyncio

    from malg.core.budget import BudgetExhausted
    from malg.core.claims import validate_account_evidence
    from malg.core.models.account import AccountData, AccountIdentity, AccountResearchResult
    from malg.core.models.research import FieldObservation

    original = AccountResearchResult(
        outcome="complete",
        qualification="accepted",
        data=AccountData(name="Company"),
        identity=AccountIdentity(display_name="Company", official_website="https://example.com"),
        observations=[
            FieldObservation(
                field="name", text="Company", quote="Altered name", excerpt_ids=["excerpt-1"]
            )
        ],
    )

    async def repair(request):
        raise BudgetExhausted(
            "Host denial", reason_code="llm_stage_limit", kind="llm", used=1, limit=1
        )

    with pytest.raises(BudgetExhausted) as error:
        asyncio.run(validate_account_evidence(original, {"excerpt-1": "Company"}, repair))
    assert error.value.diagnostics() == {
        "reason_code": "llm_stage_limit",
        "kind": "llm",
        "used": 1,
        "limit": 1,
    }


def test_unavailable_bounded_repair_leaves_candidate_unpublished() -> None:
    """Failure to finish repair must not turn a rejected candidate into a worker failure."""
    import asyncio

    from malg.core.claims import EvidenceRepairUnavailable, validate_account_evidence
    from malg.core.models.account import AccountData, AccountIdentity, AccountResearchResult
    from malg.core.models.research import FieldObservation

    original = AccountResearchResult(
        outcome="complete",
        qualification="accepted",
        data=AccountData(name="Company"),
        identity=AccountIdentity(display_name="Company", official_website="https://example.com"),
        observations=[
            FieldObservation(
                field="name", text="Company", quote="Incorrect quote", excerpt_ids=["excerpt-1"]
            )
        ],
    )

    async def repair(request):
        raise EvidenceRepairUnavailable("No valid repair within the turn allowance")

    result, claims = asyncio.run(
        validate_account_evidence(original, {"excerpt-1": "Company"}, repair)
    )
    assert result.outcome == "insufficient_evidence"
    assert result.reason_code == "invalid_evidence_quote"
    assert result.data is None
    assert not claims
