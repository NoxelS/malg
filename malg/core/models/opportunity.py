"""Bounded speculative project-opportunity proposal contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import Field, model_validator

from malg.core.models.account import Money
from malg.core.models.research import ResearchModel, ResearchOutcome


class OpportunityData(ResearchModel):
    """A project hypothesis and rough EUR fee, never an observed commercial fact."""

    name: str = Field(min_length=1, max_length=200)
    pitch: str = Field(min_length=1, max_length=2000)
    scope: str = Field(min_length=1, max_length=2000)
    deliverables: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        min_length=1, max_length=10
    )
    rationale: str = Field(min_length=1, max_length=1000)
    estimated_price: Money
    pricing_rationale: str = Field(min_length=1, max_length=1000)
    assumptions: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=10
    )

    @model_validator(mode="after")
    def validate_estimated_price(self) -> OpportunityData:
        """Require an exact, positive, finite EUR amount safe for native currency micros."""
        amount = self.estimated_price.amount
        if self.estimated_price.currency_code != "EUR":
            raise ValueError("estimated_price must use EUR")
        if not amount.is_finite() or amount <= 0:
            raise ValueError("estimated_price must be finite and positive")
        exponent = amount.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -2:
            raise ValueError("estimated_price must have at most two decimal places")
        if amount > Decimal("9007199254.74"):
            raise ValueError("estimated_price exceeds the native currency limit")
        return self


class OpportunityResearchResult(ResearchOutcome):
    """Speculative synthesis outcome, intentionally separate from sourced research."""

    data: OpportunityData | None = None

    @model_validator(mode="after")
    def validate_data_for_outcome(self) -> OpportunityResearchResult:
        """Require a proposal for publishable complete and partial outcomes."""
        if self.data is None and self.outcome in {"complete", "partial"}:
            raise ValueError("data is required for complete or partial opportunity research")
        return self
