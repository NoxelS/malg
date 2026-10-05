"""Observable denials and diagnostics for finite host-owned research budgets."""

from datetime import UTC, datetime, timedelta

import pytest

from malg.core.budget import BudgetExhausted, ResearchBudget


@pytest.mark.parametrize("kind", ["llm", "search", "fetch"])
def test_external_attempt_denial_reports_actual_count_and_limit(kind) -> None:
    budget = ResearchBudget(datetime.now(UTC) + timedelta(minutes=5), limits={kind: 2})
    budget.reserve_attempt(kind)
    budget.reserve_attempt(kind)
    with pytest.raises(BudgetExhausted) as denied:
        budget.reserve_attempt(kind)
    assert denied.value.diagnostics() == {
        "reason_code": f"{kind}_stage_limit",
        "kind": kind,
        "used": 2,
        "limit": 2,
    }
    assert budget.attempts[kind] == 2
    assert budget.exhaustion is denied.value


def test_workflow_denial_survives_adapter_wrapping() -> None:
    denial = BudgetExhausted(
        "Host workflow limit", reason_code="search_workflow_limit", kind="search", used=7, limit=7
    )

    def reserve(kind):
        raise denial

    budget = ResearchBudget(datetime.now(UTC) + timedelta(minutes=5), reserve=reserve)
    with pytest.raises(BudgetExhausted):
        budget.reserve_attempt("search")
    assert budget.exhaustion is denial
    assert budget.attempts["search"] == 0
    assert budget.exhausted


def test_deadline_denial_identifies_workflow_time() -> None:
    budget = ResearchBudget(
        datetime.now(UTC) - timedelta(seconds=1), deadline_reason="workflow_deadline_exhausted"
    )
    with pytest.raises(BudgetExhausted) as denied:
        budget.checkpoint()
    assert denied.value.reason_code == "workflow_deadline_exhausted"
    assert denied.value.diagnostics()["kind"] == "time"
    assert denied.value.diagnostics()["limit"] == 0
