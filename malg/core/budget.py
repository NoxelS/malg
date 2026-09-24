"""Finite monotonic budgets shared by research stages and their attempts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from time import monotonic
from typing import Literal


class BudgetExhausted(RuntimeError):
    """Raised when work would consume the cleanup reserve."""


class ClaimLost(RuntimeError):
    """Raised when a supervised stage no longer owns its durable job."""


class ResearchBudget:
    """A monotonic deadline with bounded per-kind attempt reservations.

    The persisted UTC deadline is converted to a monotonic deadline once at
    construction. A child budget can only shorten the parent deadline.
    """

    def __init__(
        self,
        deadline_at: datetime,
        *,
        cleanup_reserve_seconds: float = 5.0,
        monotonic_now: float | None = None,
        limits: Mapping[str, int] | None = None,
        reserve: Callable[[Literal["llm", "search", "fetch"]], None] | None = None,
    ) -> None:
        if deadline_at.tzinfo is None:
            raise ValueError("deadline_at must be timezone-aware")
        self.deadline_at = deadline_at.astimezone(UTC)
        self.cleanup_reserve_seconds = cleanup_reserve_seconds
        if cleanup_reserve_seconds < 0:
            raise ValueError("cleanup_reserve_seconds must be non-negative")
        remaining = (self.deadline_at - datetime.now(UTC)).total_seconds()
        self._deadline = (monotonic() if monotonic_now is None else monotonic_now) + remaining
        self._attempts = {"llm": 0, "search": 0, "fetch": 0}
        self._limits = dict(limits or {})
        self._reserve = reserve
        self._exhausted = False

    def remaining_seconds(self) -> float:
        """Return seconds available before the cleanup reserve."""
        return max(0.0, self._deadline - monotonic() - self.cleanup_reserve_seconds)

    def timeout_seconds(self, cap: float) -> float:
        """Clip a positive operation cap to the remaining budget."""
        if cap <= 0:
            raise ValueError("cap must be positive")
        remaining = self.remaining_seconds()
        if remaining <= 0:
            self._exhausted = True
            raise BudgetExhausted("research cleanup reserve reached")
        return min(float(cap), remaining)

    def checkpoint(self) -> None:
        """Fail closed when no stage work can safely begin."""
        if self.remaining_seconds() <= 0:
            self._exhausted = True
            raise BudgetExhausted("research cleanup reserve reached")

    def reserve_attempt(self, kind: Literal["llm", "search", "fetch"]) -> None:
        """Reserve one external attempt before making its request."""
        self.checkpoint()
        if self._attempts[kind] >= self._limits.get(kind, float("inf")):
            self._exhausted = True
            raise BudgetExhausted(f"{kind} attempt budget exhausted")
        if self._reserve is not None:
            try:
                self._reserve(kind)
            except BudgetExhausted:
                self._exhausted = True
                raise
        self._attempts[kind] += 1

    def child(self, stage_deadline_at: datetime) -> ResearchBudget:
        """Shorten the deadline while retaining the parent's shared attempt limits."""
        if stage_deadline_at.tzinfo is None:
            raise ValueError("stage_deadline_at must be timezone-aware")
        deadline = min(self.deadline_at, stage_deadline_at.astimezone(UTC))
        return ResearchBudget(
            deadline,
            cleanup_reserve_seconds=self.cleanup_reserve_seconds,
            limits=self._limits,
            reserve=self.reserve_attempt,
        )

    @property
    def exhausted(self) -> bool:
        """Retain actual budget denial even if a reasoning SDK wraps the exception."""
        return self._exhausted

    @property
    def attempts(self) -> dict[str, int]:
        """Return a snapshot of locally reserved attempt counts."""
        return dict(self._attempts)


_active_budget: ContextVar[ResearchBudget | None] = ContextVar("research_budget", default=None)


def bind_budget(budget: ResearchBudget) -> Callable[[], None]:
    """Bind a stage budget to async work and return its context-reset callback."""
    token = _active_budget.set(budget)
    return lambda: _active_budget.reset(token)


def reserve_external_attempt(kind: Literal["llm", "search", "fetch"]) -> None:
    """Fence and reserve an actual external request when a stage budget is bound."""
    budget = _active_budget.get()
    if budget is not None:
        budget.reserve_attempt(kind)
