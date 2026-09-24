"""Shared bounded contracts for evidence-backed research stages."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchModel(BaseModel):
    """Strict base for host-validated research payloads."""

    model_config = ConfigDict(extra="forbid")


class ClaimProposal(ResearchModel):
    """A sourced claim referencing host-supplied excerpt IDs."""

    text: str = Field(min_length=1, max_length=500)
    excerpt_ids: list[str] = Field(min_length=1, max_length=8)
    quote: str = Field(min_length=1, max_length=500)


class FieldObservation(ClaimProposal):
    """A sourced proposal naming an exact field of the returned business model.

    Nested observations may be supplied as dictionaries; no separate constructor
    import is required by generated code.
    """

    field: str = Field(min_length=1, max_length=64)


class ResearchOutcome(ResearchModel):
    """Public disposition and bounded unknowns, independent of transport status."""

    outcome: Literal[
        "complete", "partial", "needs_review", "insufficient_evidence", "budget_exhausted"
    ]
    unknowns: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=10
    )


class ResearchResult[T: BaseModel](ResearchOutcome):
    """Bounded generation result; host owns IDs, parents, and persistence."""

    data: T | None = None
    observations: list[FieldObservation] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_data_for_outcome(self) -> ResearchResult[T]:
        """Reject missing publishable data and observations outside its business fields."""
        if self.data is None and self.outcome not in {
            "insufficient_evidence",
            "budget_exhausted",
            "needs_review",
        }:
            raise ValueError("data is required for complete or partial research")
        if self.data is not None:
            fields = type(self.data).model_fields
            for observation in self.observations:
                if observation.field not in fields:
                    raise ValueError(
                        f"unknown business field {observation.field!r}; "
                        f"expected one of {', '.join(fields)}"
                    )
        return self
