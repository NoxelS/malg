"""Boundary tests for speculative opportunity contracts."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from malg.core.models.opportunity import OpportunityData, OpportunityResearchResult


def proposal(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "Intake automation",
        "pitch": "Reduce manual document handling with a bounded workflow improvement.",
        "scope": "Map the intake path and implement one review workflow.",
        "deliverables": ["Workflow design", "Implementation handoff"],
        "rationale": "The workflow is aligned with the stated objective.",
        "estimated_price": {"amount": Decimal("12500.50"), "currency_code": "EUR"},
        "pricing_rationale": "Assumes 25 days at an explicitly rough EUR 500 daily rate.",
    }
    value.update(overrides)
    return value


def test_accepts_exact_eur_estimate() -> None:
    data = OpportunityData.model_validate(proposal())
    assert data.estimated_price.amount == Decimal("12500.50")


@pytest.mark.parametrize(
    "estimate",
    [
        {"amount": Decimal("0"), "currency_code": "EUR"},
        {"amount": Decimal("-1"), "currency_code": "EUR"},
        {"amount": Decimal("1.001"), "currency_code": "EUR"},
        {"amount": Decimal("9007199254.75"), "currency_code": "EUR"},
        {"amount": Decimal("1"), "currency_code": "USD"},
    ],
)
def test_rejects_unsafe_estimates(estimate: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OpportunityData.model_validate(proposal(estimated_price=estimate))


@pytest.mark.parametrize("field", ["scope", "deliverables", "estimated_price", "pricing_rationale"])
def test_required_proposal_fields(field: str) -> None:
    value = proposal()
    value.pop(field)
    with pytest.raises(ValidationError):
        OpportunityData.model_validate(value)


@pytest.mark.parametrize("outcome", ["complete", "partial"])
def test_publishable_outcomes_require_data(outcome: str) -> None:
    with pytest.raises(ValidationError):
        OpportunityResearchResult.model_validate({"outcome": outcome})


@pytest.mark.parametrize("outcome", ["insufficient_evidence", "budget_exhausted"])
def test_nonpublishable_outcomes_may_omit_data(outcome: str) -> None:
    result = OpportunityResearchResult.model_validate({"outcome": outcome})
    assert result.data is None


def test_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        OpportunityData.model_validate({**proposal(), "remote_id": "forbidden"})
