"""Behavioral tests for inert bounded-discovery value contracts."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from malg.core.models.discovery import (
    CompanyICPAssessment,
    CompanyPublicationState,
    DiscoveryBatchInput,
    DiscoveryBatchResult,
    DiscoveryBudgetLimits,
    DiscoveryCompanyState,
    DiscoveryCounts,
    DiscoveryRetrievalSummary,
    DiscoveryScope,
    DiscoverySourcePolicy,
    DiscoveryStopReason,
    validate_batch_transition,
)


def scope(**changes):
    values = dict(
        scope_id=uuid4(),
        revision=1,
        enabled=True,
        source_policy=DiscoverySourcePolicy(reuse_sources=False, follow_observed_links=False),
        budgets=DiscoveryBudgetLimits(
            max_pages=10, max_requests=20, max_bytes=1048576, max_seconds=60, max_model_calls=5
        ),
    )
    values.update(changes)
    return DiscoveryScope(**values)


def result(status, **changes):
    return DiscoveryBatchResult(batch_id=UUID, status=status, checkpoint_sequence=0, **changes)


UUID = uuid4()


def test_batch_has_finite_snapshot_without_campaign_or_icp():
    now = datetime.now(UTC)
    batch = DiscoveryBatchInput(
        batch_id=uuid4(), scope=scope(), started_at=now, deadline_at=now + timedelta(seconds=60)
    )
    assert batch.scope.source_policy.seed_urls == []
    with pytest.raises(ValidationError):
        DiscoveryBudgetLimits(
            max_pages=True, max_requests=1, max_bytes=1, max_seconds=1, max_model_calls=1
        )
    with pytest.raises(ValidationError):
        DiscoveryScope(
            schema_version=2,
            scope_id=uuid4(),
            revision=1,
            enabled=True,
            source_policy=DiscoverySourcePolicy(reuse_sources=True, follow_observed_links=False),
            budgets=DiscoveryBudgetLimits(
                max_pages=1, max_requests=1, max_bytes=1, max_seconds=1, max_model_calls=1
            ),
        )
    with pytest.raises(ValidationError):
        DiscoveryBatchInput(
            batch_id=uuid4(),
            scope=scope(enabled=False),
            started_at=now,
            deadline_at=now + timedelta(seconds=1),
        )
    with pytest.raises(ValidationError):
        DiscoveryBatchInput(
            batch_id=uuid4(), scope=scope(), started_at=now, deadline_at=now + timedelta(seconds=61)
        )


@pytest.mark.parametrize("value", [0, 1, "true", "false"])
def test_discovery_policy_and_scope_booleans_are_strict(value):
    with pytest.raises(ValidationError):
        DiscoverySourcePolicy(reuse_sources=value, follow_observed_links=False)
    with pytest.raises(ValidationError):
        DiscoverySourcePolicy(reuse_sources=False, follow_observed_links=value)
    with pytest.raises(ValidationError):
        scope(enabled=value)


def test_result_validation_health_and_canonical_roundtrip():
    batch_id = uuid4()
    healthy = DiscoveryRetrievalSummary(usable=1)
    completed = DiscoveryBatchResult(
        batch_id=batch_id,
        status="succeeded",
        retrieval=healthy,
        counts=DiscoveryCounts(observed_companies=5, new_companies=0, known_companies=5),
        checkpoint_sequence=1,
        stop_reason="work_completed",
    )
    assert completed.retrieval.health == "healthy"
    payload = completed.model_dump(mode="json", round_trip=True)
    assert "health" not in payload["retrieval"]
    assert DiscoveryBatchResult.model_validate(payload) == completed
    budget = DiscoveryBatchResult(
        batch_id=batch_id,
        status="succeeded",
        retrieval=healthy,
        counts=DiscoveryCounts(observed_companies=5, new_companies=2, known_companies=3),
        checkpoint_sequence=1,
        stop_reason="budget_exhausted",
        exhausted_budget="pages",
    )
    assert budget.counts.observed_companies == 5
    deferred = DiscoveryBatchResult(
        batch_id=batch_id,
        status="succeeded",
        retrieval=DiscoveryRetrievalSummary(deferred=2),
        checkpoint_sequence=0,
        stop_reason="source_access_deferred",
    )
    assert deferred.retrieval.health == "unavailable"
    with pytest.raises(ValidationError):
        DiscoveryRetrievalSummary(usable=1, health="unavailable")
    with pytest.raises(ValidationError):
        DiscoveryBatchResult(
            batch_id=batch_id,
            status="succeeded",
            retrieval=DiscoveryRetrievalSummary(failed=1),
            checkpoint_sequence=0,
            stop_reason="work_completed",
        )
    with pytest.raises(ValidationError):
        DiscoveryBatchResult(
            batch_id=batch_id,
            status="succeeded",
            retrieval=DiscoveryRetrievalSummary(deferred=1),
            checkpoint_sequence=0,
            stop_reason="no_eligible_work",
        )
    assert DiscoveryStopReason.BUDGET_EXHAUSTED == "budget_exhausted"


def test_concurrent_batches_allocate_global_new_credit_once():
    """A concurrent observer can record a company without claiming first-discovery credit."""
    first_credit = DiscoveryCounts(observed_companies=1, new_companies=1, known_companies=0)
    concurrent_observation = DiscoveryCounts(
        observed_companies=1, new_companies=0, known_companies=1
    )
    assert first_credit.observed_companies + concurrent_observation.observed_companies == 2
    assert first_credit.new_companies + concurrent_observation.new_companies == 1
    assert first_credit.known_companies + concurrent_observation.known_companies == 1


def test_transition_progress_is_monotonic_and_terminals_are_immutable():
    batch_id = uuid4()
    queued = DiscoveryBatchResult(batch_id=batch_id, status="queued", checkpoint_sequence=0)
    running = DiscoveryBatchResult(batch_id=batch_id, status="running", checkpoint_sequence=0)
    progress = DiscoveryBatchResult(
        batch_id=batch_id,
        status="running",
        retrieval=DiscoveryRetrievalSummary(usable=1),
        counts=DiscoveryCounts(observed_companies=5, new_companies=2, known_companies=3),
        checkpoint_sequence=1,
    )
    terminal = DiscoveryBatchResult(
        batch_id=batch_id,
        status="succeeded",
        retrieval=progress.retrieval,
        counts=progress.counts,
        checkpoint_sequence=1,
        stop_reason="budget_exhausted",
        exhausted_budget="pages",
    )
    validate_batch_transition(queued, running)
    validate_batch_transition(running, progress)
    validate_batch_transition(progress, terminal)
    validate_batch_transition(terminal, terminal.model_copy(deep=True))
    reclassified = DiscoveryBatchResult(
        batch_id=batch_id,
        status="running",
        retrieval=progress.retrieval,
        counts=DiscoveryCounts(observed_companies=5, new_companies=1, known_companies=4),
        checkpoint_sequence=2,
    )
    retrieval_progress = DiscoveryBatchResult(
        batch_id=batch_id,
        status="running",
        retrieval=DiscoveryRetrievalSummary(usable=1, deferred=1),
        counts=progress.counts,
        checkpoint_sequence=1,
    )
    retrieval_progress_checkpointed = retrieval_progress.model_copy(
        update={"checkpoint_sequence": 2}
    )
    with pytest.raises(ValueError, match="new checkpoint"):
        validate_batch_transition(progress, retrieval_progress)
    validate_batch_transition(progress, retrieval_progress_checkpointed)
    for before, after in (
        (terminal, progress),
        (queued, running.model_copy(update={"batch_id": uuid4()})),
        (progress, running),
        (progress, reclassified),
        (running, progress.model_copy(update={"checkpoint_sequence": 0})),
    ):
        with pytest.raises(ValueError, match=r"terminal|identity|progress|counts"):
            validate_batch_transition(before, after)


def test_company_assessment_and_publication_are_independent():
    company_id, icp_a, icp_b = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    company = DiscoveryCompanyState(
        company_id=company_id,
        data={"name": "Provisional"},
        identity_state="provisional",
        evidence_version=1,
        source_refs=["source:1"],
        first_observed_at=now,
        last_observed_at=now,
    )
    assert company.identity is None
    match = CompanyICPAssessment(
        company_id=company_id,
        evidence_version=1,
        icp_id=icp_a,
        icp_version="a1",
        evaluator_version="e1",
        outcome="match",
        claim_refs=["claim:1"],
    )
    mismatch = CompanyICPAssessment(
        company_id=company_id,
        evidence_version=1,
        icp_id=icp_b,
        icp_version="b1",
        evaluator_version="e1",
        outcome="mismatch",
        claim_refs=["claim:2"],
    )
    revised = match.model_copy(update={"icp_version": "a2"})
    assert match.icp_id != mismatch.icp_id
    assert revised.evidence_version == company.evidence_version
    assert (
        CompanyPublicationState(company_id=company_id, status="not_published").remote_company_id
        is None
    )
    with pytest.raises(ValidationError):
        CompanyICPAssessment(
            company_id=company_id,
            evidence_version=0,
            icp_id=icp_a,
            icp_version="a1",
            evaluator_version="e1",
            outcome="match",
            claim_refs=[],
        )
    with pytest.raises(ValidationError):
        CompanyICPAssessment(
            company_id=company_id,
            evidence_version=0,
            icp_id=icp_a,
            icp_version="a1",
            evaluator_version="e1",
            outcome="insufficient_evidence",
        )
