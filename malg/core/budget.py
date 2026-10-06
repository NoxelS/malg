"""Finite monotonic budgets shared by research stages and their attempts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, NoReturn


class BudgetExhausted(RuntimeError):
    """A host-enforced denial with safe limit and usage diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "research_budget_exhausted",
        kind: str | None = None,
        used: int | float | None = None,
        limit: int | float | None = None,
        stage_key: str | None = None,
    ) -> None:
        """Retain host-owned counters without including upstream or generated payloads."""
        super().__init__(message)
        self.reason_code, self.kind = reason_code, kind
        self.used, self.limit = used, limit
        self.stage_key = stage_key

    def diagnostics(self) -> dict[str, str | int | float]:
        """Return bounded public metadata identifying the exhausted resource."""
        result: dict[str, str | int | float] = {"reason_code": self.reason_code}
        if self.kind is not None:
            result["kind"] = self.kind
        if self.used is not None:
            result["used"] = self.used
        if self.limit is not None:
            result["limit"] = self.limit
        if self.stage_key is not None:
            result["stage_key"] = self.stage_key
        return result


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
        deadline_reason: str = "stage_deadline_exhausted",
    ) -> None:
        if deadline_at.tzinfo is None:
            raise ValueError("deadline_at must be timezone-aware")
        self.deadline_at = deadline_at.astimezone(UTC)
        self.cleanup_reserve_seconds = cleanup_reserve_seconds
        if cleanup_reserve_seconds < 0:
            raise ValueError("cleanup_reserve_seconds must be non-negative")
        remaining = (self.deadline_at - datetime.now(UTC)).total_seconds()
        self._started = monotonic() if monotonic_now is None else monotonic_now
        self._duration = remaining
        self._deadline = self._started + remaining
        self._deadline_reason = deadline_reason
        self._attempts = {"llm": 0, "search": 0, "fetch": 0}
        self._limits = dict(limits or {})
        self._reserve = reserve
        self._exhaustion: BudgetExhausted | None = None

    def remaining_seconds(self) -> float:
        """Return seconds available before the cleanup reserve."""
        return max(0.0, self._deadline - monotonic() - self.cleanup_reserve_seconds)

    def timeout_seconds(self, cap: float) -> float:
        """Clip a positive operation cap to the remaining budget."""
        if cap <= 0:
            raise ValueError("cap must be positive")
        remaining = self.remaining_seconds()
        if remaining <= 0:
            self._exhaustion = self._deadline_denial()
            raise self._exhaustion
        return min(float(cap), remaining)

    def _deadline_denial(self) -> BudgetExhausted:
        """Describe usable stage time, keeping the cleanup reserve outside its limit."""
        return BudgetExhausted(
            "research cleanup reserve reached",
            reason_code=self._deadline_reason,
            kind="time",
            used=max(0.0, monotonic() - self._started),
            limit=max(0.0, self._duration - self.cleanup_reserve_seconds),
        )

    def checkpoint(self) -> None:
        """Fail closed when no stage work can safely begin."""
        if self.remaining_seconds() <= 0:
            self._exhaustion = self._deadline_denial()
            raise self._exhaustion

    def reserve_attempt(self, kind: Literal["llm", "search", "fetch"]) -> None:
        """Reserve one external attempt before making its request."""
        self.checkpoint()
        if self._attempts[kind] >= self._limits.get(kind, float("inf")):
            self._exhaustion = BudgetExhausted(
                f"{kind} attempt budget exhausted",
                reason_code=f"{kind}_stage_limit",
                kind=kind,
                used=self._attempts[kind],
                limit=self._limits[kind],
            )
            raise self._exhaustion
        if self._reserve is not None:
            try:
                self._reserve(kind)
            except BudgetExhausted as error:
                self._exhaustion = error
                raise
        self._attempts[kind] += 1

    def deny(self, error: BudgetExhausted) -> NoReturn:
        """Retain and raise a host-owned adapter limit even if generated code catches it."""
        self._exhaustion = error
        raise error

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
            deadline_reason=(
                self._deadline_reason
                if deadline == self.deadline_at
                else "stage_deadline_exhausted"
            ),
        )

    @property
    def exhausted(self) -> bool:
        """Retain actual budget denial even if a reasoning SDK wraps the exception."""
        return self._exhaustion is not None

    @property
    def exhaustion(self) -> BudgetExhausted | None:
        """Return the actual denial even when an adapter catches or wraps its exception."""
        return self._exhaustion

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


def deny_external_attempt(error: BudgetExhausted) -> NoReturn:
    """Raise an adapter's host-owned limit and retain it in the active stage budget."""
    budget = _active_budget.get()
    if budget is not None:
        budget.deny(error)
    raise error
