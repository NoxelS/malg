"""Finite monotonic budgets shared by research stages and their attempts."""

from __future__ import annotations

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

    def remaining_seconds(self) -> float:
        """Return seconds available before the cleanup reserve."""
        return max(0.0, self._deadline - monotonic() - self.cleanup_reserve_seconds)

    def timeout_seconds(self, cap: float) -> float:
        """Clip a positive operation cap to the remaining budget."""
        if cap <= 0:
            raise ValueError("cap must be positive")
        remaining = self.remaining_seconds()
        if remaining <= 0:
            raise BudgetExhausted("research cleanup reserve reached")
        return min(float(cap), remaining)

    def checkpoint(self) -> None:
        """Fail closed when no stage work can safely begin."""
        if self.remaining_seconds() <= 0:
            raise BudgetExhausted("research cleanup reserve reached")

    def reserve_attempt(self, kind: Literal["llm", "search", "fetch"]) -> None:
        """Reserve one external attempt before making its request."""
        self.checkpoint()
        self._attempts[kind] += 1

    def child(self, stage_deadline_at: datetime) -> ResearchBudget:
        """Return a child budget clipped to this budget's deadline."""
        deadline = min(self.deadline_at, stage_deadline_at.astimezone(UTC))
        return ResearchBudget(deadline, cleanup_reserve_seconds=self.cleanup_reserve_seconds)

    @property
    def attempts(self) -> dict[str, int]:
        """Return a snapshot of locally reserved attempt counts."""
        return dict(self._attempts)
