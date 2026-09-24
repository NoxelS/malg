"""Lean ideal-customer-profile contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ICPData(BaseModel):
    """Canonical ICP fields owned by Twenty."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    sector: str = Field(min_length=1, max_length=200)
    geography: str = Field(min_length=1, max_length=200)
    employees_min: int | None = Field(default=None, strict=True, ge=0)
    employees_max: int | None = Field(default=None, strict=True, ge=0)
    buyer_role: str = Field(min_length=1, max_length=200)
    workflow: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_employee_range(self) -> ICPData:
        """Reject contradictory exact employee bounds without guessing unknown values."""
        if (
            self.employees_min is not None
            and self.employees_max is not None
            and self.employees_min > self.employees_max
        ):
            raise ValueError("employees_min must not exceed employees_max")
        return self
