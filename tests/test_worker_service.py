"""Worker outcomes across durable checkpoints, live scope changes and cancellation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from twenty_fake import publication_fixture

from malg.config import WorkerConfig
from malg.core.models.account import (
    AccountData,
    AccountEngagementSignal,
    AccountIdentity,
    AccountResearchResult,
    AccountValidationAssessment,
    CheckOutcome,
    Money,
    ValidationCheck,
)
from malg.core.models.campaign import CampaignData
from malg.core.models.icp import ICPData
from malg.core.models.jobs import (
    AccountResearchJobRequest,
    CampaignResearchJobRequest,
    ICPResearchJobRequest,
)
from malg.core.models.opportunity import OpportunityData, OpportunityResearchResult
from malg.core.models.person import PersonData
from malg.core.models.research import FieldObservation, ResearchResult
from malg.database.jobs import claim_next_job, enqueue_job, fail_job, retry_failed_job
from malg.database.models import (
    ResearchJob,
    ResearchStageResult,
    ResearchWorkflow,
    WorkerHeartbeat,
)
from malg.database.research import create_workflow_for_job, persist_stage_result
from malg.worker.service import ResearchWorker


@pytest.fixture
def setup_worker(twenty_metadata):
    """Create workers with actual adapters and persistence; no live generation is invoked."""
    fixtures = []

    def create(request=None):
        fixture = publication_fixture(twenty_metadata, request)
        fixtures.append(fixture)
        return fixture

    yield create
    for fixture in fixtures:
        asyncio.run(fixture[3].aclose())
        fixture[0].dispose()


def worker_for(fixture) -> ResearchWorker:
    """Use a one-second lease so cancellation is observed without long sleeps."""
    return ResearchWorker(
        fixture[1], WorkerConfig(lease_seconds=1), crm_client=fixture[4], crm_publisher=fixture[5]
    )


def requeue(fixture) -> None:
    """Simulate an explicit retry while preserving stable job identity and history."""
    sessions, job = fixture[1], fixture[6]
    with sessions.begin() as session:
        fail_job(
            session, job.job_id, job.claim_token, "interrupted after checkpoint", datetime.now(UTC)
        )
        retry_failed_job(session, job.job_id, datetime.now(UTC))


async def save_checkpoint(fixture, result) -> str:
    """Persist an already-validated research checkpoint before simulating a crash."""
    sessions, client, job = fixture[1], fixture[4], fixture[6]
    records = await client.read_scope(job.request_payload)
    payload = {
        "request": job.request_payload,
        "crm_inputs": {key: value.targeting_snapshot() for key, value in records.items()},
        "observed_records": {key: value.model_dump(mode="json") for key, value in records.items()},
    }
    with sessions.begin() as session:
        current = session.get(ResearchJob, job.job_id)
        workflow = create_workflow_for_job(
            session, current, payload, datetime.now(UTC) + timedelta(minutes=5)
        )
        persist_stage_result(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            workflow_id=workflow.workflow_id,
            stage_key=("icp.candidate.0.research" if job.kind == "icp" else f"{job.kind}.research"),
            input_hash=workflow.input_hash,
            outcome=result.outcome,
            payload=result.model_dump(mode="json"),
            now=datetime.now(UTC),
        )
        workflow_id = workflow.workflow_id
    requeue(fixture)
    return workflow_id


def test_heartbeat_refreshes_without_crm_availability(setup_worker) -> None:
    fixture = setup_worker()
    fixture[2].unavailable = True
    worker = worker_for(fixture)
    asyncio.run(worker.heartbeat())
    asyncio.run(worker.heartbeat())
    with fixture[1]() as session:
        rows = list(session.scalars(select(WorkerHeartbeat)))
        assert len(rows) == 1
        assert rows[0].worker_token == worker.worker_token
        assert rows[0].last_seen_at >= rows[0].created_at


def test_restart_claims_only_expired_work_and_preserves_attempt_history(setup_worker) -> None:
    fixture = setup_worker()
    sessions, running = fixture[1], fixture[6]
    with sessions.begin() as session:
        other = enqueue_job(CampaignResearchJobRequest(), session)
        claimed = claim_next_job(session, "other-worker", datetime.now(UTC), 60, 3)
        assert claimed.job_id == other.job_id
        original_started = claimed.started_at
        claimed.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with sessions.begin() as session:
        recovered = claim_next_job(session, "replacement", datetime.now(UTC), 60, 3)
        assert recovered.job_id == other.job_id
        assert recovered.attempt_count == 2
        assert recovered.started_at.replace(tzinfo=UTC) == original_started
        assert session.get(ResearchJob, running.job_id).claim_token == running.claim_token


def test_campaign_retry_reuses_workflow_and_completed_research(setup_worker) -> None:
    async def run() -> None:
        fixture = setup_worker()
        workflow_id = await save_checkpoint(
            fixture,
            ResearchResult[CampaignData](
                outcome="complete",
                data=CampaignData(name="Saved research", objective="Sourced objective"),
            ),
        )
        assert await worker_for(fixture).run_once()
        with fixture[1]() as session:
            job = session.get(ResearchJob, fixture[6].job_id)
            assert job.status == "succeeded"
            assert job.result_outcome == "complete"
            assert job.attempt_count == 2
            assert job.workflow_id == workflow_id
            assert session.get(ResearchWorkflow, workflow_id).status == "succeeded"
            research = list(
                session.scalars(
                    select(ResearchStageResult).where(
                        ResearchStageResult.stage_key == "campaign.research"
                    )
                )
            )
            assert len(research) == 1
            assert research[0].revision == 1
            assert job.result_refs[0]["record_id"] == fixture[2].creates[0][1]
        assert len(fixture[2].creates) == 1

    asyncio.run(run())


def test_icp_uses_human_created_remote_campaign_without_local_business_history(
    setup_worker,
) -> None:
    async def run() -> None:
        campaign_id = uuid4()
        fixture = setup_worker(ICPResearchJobRequest(campaign_id=campaign_id))
        fixture[2].add(
            "malgCampaign",
            {"name": "Human campaign", "objective": "Observed scope"},
            str(campaign_id),
        )
        result = ResearchResult[ICPData](
            outcome="complete",
            data=ICPData(
                name="Profile",
                sector="Manufacturing",
                geography="Germany",
                buyer_role="Operations",
                workflow="Supplier qualification",
            ),
        )
        await save_checkpoint(fixture, result)
        assert await worker_for(fixture).run_once()
        assert len(fixture[2].creates) == 1
        kind, identifier = fixture[2].creates[0]
        assert kind == "malgIcp"
        assert fixture[2].records[kind, identifier]["campaignId"] == str(campaign_id)

    asyncio.run(run())


def test_icp_invalid_evidence_candidate_does_not_abort_remaining_candidates(
    setup_worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fail-closed ICP candidate leaves enough budget to find a later valid one."""

    async def run() -> None:
        campaign_id = uuid4()
        fixture = setup_worker(ICPResearchJobRequest(campaign_id=campaign_id, icp_count=1))
        fixture[2].add(
            "malgCampaign",
            {"name": "Human campaign", "objective": "Observed scope"},
            str(campaign_id),
        )
        worker = worker_for(fixture)
        requeue(fixture)
        results = iter(
            [
                ResearchResult[ICPData](
                    outcome="insufficient_evidence",
                    unknowns=["invalid_evidence_reference"],
                ),
                ResearchResult[ICPData](
                    outcome="complete",
                    data=ICPData(
                        name="Validated profile",
                        sector="Manufacturing",
                        geography="Germany",
                        buyer_role="Operations",
                        workflow="Supplier qualification",
                    ),
                ),
            ]
        )

        async def research(*_args, **_kwargs):
            return next(results)

        monkeypatch.setattr(worker, "_research", research)

        assert await worker.run_once()
        with fixture[1]() as session:
            job = session.get(ResearchJob, fixture[6].job_id)
            assert job.status == "succeeded"
            assert job.result_outcome == "complete"
            publication = session.scalar(
                select(ResearchStageResult).where(
                    ResearchStageResult.stage_key == "icp.publication"
                )
            )
            assert publication.payload["attempts"] == 2
        assert len(fixture[2].creates) == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["changed", "deleted"])
def test_changed_or_deleted_parent_prevents_any_new_publication(setup_worker, change) -> None:
    async def run() -> None:
        campaign_id = uuid4()
        fixture = setup_worker(ICPResearchJobRequest(campaign_id=campaign_id))
        remote = fixture[2]
        remote.add(
            "malgCampaign",
            {"name": "Human campaign", "objective": "Original scope"},
            str(campaign_id),
        )
        result = ResearchResult[ICPData](
            outcome="complete",
            data=ICPData(
                name="Profile",
                sector="Manufacturing",
                geography="Germany",
                buyer_role="Operations",
                workflow="Supplier qualification",
            ),
        )
        await save_checkpoint(fixture, result)
        reads = 0

        def change_before_publication(kind):
            nonlocal reads
            if kind == "malgCampaign":
                reads += 1
                if reads == 2:
                    if change == "deleted":
                        del remote.records[kind, str(campaign_id)]
                    else:
                        remote.edit(kind, str(campaign_id), {"objective": "Changed targeting"})

        remote.before_read = change_before_publication
        await worker_for(fixture).run_once()
        with fixture[1]() as session:
            job = session.get(ResearchJob, fixture[6].job_id)
            assert job.status == "failed"
            assert job.failure_detail == (
                "crm_record_missing" if change == "deleted" else "crm_input_changed"
            )
        assert remote.creates == []

    asyncio.run(run())


@pytest.mark.parametrize(
    ("rejected_candidates", "local_limit"),
    [(0, None), (4, None), (1, "stage_deadline_exhausted"), (1, "search_stage_limit")],
)
def test_account_job_publishes_complete_company_person_opportunity_bundle(
    setup_worker, rejected_candidates, local_limit
) -> None:
    async def run() -> None:
        campaign_id, icp_id = uuid4(), uuid4()
        fixture = setup_worker(
            AccountResearchJobRequest(
                icp_id=icp_id,
                company_count=1,
                people_per_company=2,
                opportunities_per_company=2,
            )
        )
        remote = fixture[2]
        remote.add("malgCampaign", {"name": "Human", "objective": "Scope"}, str(campaign_id))
        remote.add(
            "malgIcp",
            {
                "name": "Profile",
                "sector": "Software",
                "geography": "Europe",
                "employeesMin": None,
                "employeesMax": None,
                "buyerRole": "Founder",
                "workflow": "CRM",
                "campaignId": str(campaign_id),
            },
            str(icp_id),
        )
        account = AccountResearchResult(
            outcome="complete",
            data=AccountData(
                name="Proof Company", sector="Software", website="https://proof.example"
            ),
            identity=AccountIdentity(
                display_name="Proof Company", official_website="https://proof.example"
            ),
            engagement_signal=AccountEngagementSignal(
                signal_type="subcontractor_request",
                title="Freelance delivery support",
                source_url="https://proof.example/partners",
                invited_work="Support client software delivery projects.",
                response_route="Apply through the published partner form.",
                status="open",
            ),
            signal_observations=[
                FieldObservation(
                    field="response_route",
                    text="The company publishes a partner application route.",
                    excerpt_ids=["excerpt-1"],
                    quote="Apply through our partner form",
                )
            ],
            qualification="accepted",
        )
        validation = AccountValidationAssessment(
            outcome="accepted",
            rationale="The official site confirms the identity and fit.",
            checks=[
                ValidationCheck(
                    check_type="identity",
                    target="proof.example",
                    outcome=CheckOutcome.PASS,
                    reason="Observed official domain",
                )
            ],
            validated_at=datetime.now(UTC),
        )
        first_person = ResearchResult[PersonData](
            outcome="complete",
            data=PersonData(first_name="Ada", last_name="Lovelace", job_title="Founder"),
        )
        second_person = ResearchResult[PersonData](
            outcome="complete",
            data=PersonData(first_name="Grace", last_name="Hopper", job_title="CTO"),
        )
        first_opportunity = OpportunityResearchResult(
            outcome="complete",
            data=OpportunityData(
                name="CRM workflow assessment",
                pitch="Assess the current CRM workflow and identify bounded improvements.",
                scope="Review the existing process and prepare an implementation plan.",
                deliverables=["Workflow assessment", "Implementation plan"],
                rationale="The ICP identifies CRM as the relevant workflow.",
                estimated_price=Money(amount="5000", currency_code="EUR"),
                pricing_rationale="Five consulting days at an assumed blended rate.",
            ),
        )
        second_opportunity = OpportunityResearchResult(
            outcome="complete",
            data=OpportunityData(
                name="CRM implementation sprint",
                pitch="Implement the highest-priority workflow improvement.",
                scope="Deliver one bounded CRM workflow improvement.",
                deliverables=["Configured workflow", "Handover notes"],
                rationale="The assessment creates a bounded implementation opportunity.",
                estimated_price=Money(amount="8000", currency_code="EUR"),
                pricing_rationale="Eight consulting days at an assumed blended rate.",
            ),
        )
        records = await fixture[4].read_scope(fixture[6].request_payload)
        input_payload = {
            "request": fixture[6].request_payload,
            "crm_inputs": {key: value.targeting_snapshot() for key, value in records.items()},
            "observed_records": {
                key: value.model_dump(mode="json") for key, value in records.items()
            },
        }
        with fixture[1].begin() as session:
            current = session.get(ResearchJob, fixture[6].job_id)
            workflow = create_workflow_for_job(
                session, current, input_payload, datetime.now(UTC) + timedelta(minutes=20)
            )
            for index in range(rejected_candidates):
                rejected = AccountResearchResult(
                    outcome="budget_exhausted" if local_limit else "insufficient_evidence",
                    qualification="needs_review",
                    unknowns=[local_limit or "No qualifying invitation"],
                )
                rejected_payload = rejected.model_dump(mode="json")
                if local_limit:
                    rejected_payload["budget"] = {
                        "reason_code": local_limit,
                        "kind": "time" if local_limit == "stage_deadline_exhausted" else "search",
                        "used": 20,
                        "limit": 20,
                    }
                persist_stage_result(
                    session,
                    job_id=current.job_id,
                    claim_token=current.claim_token,
                    workflow_id=workflow.workflow_id,
                    stage_key=f"account.candidate.{index}.research",
                    input_hash=workflow.input_hash,
                    outcome=rejected.outcome,
                    payload=rejected_payload,
                    now=datetime.now(UTC),
                )
            prefix = f"account.candidate.{rejected_candidates}"
            for stage_key, result in (
                (f"{prefix}.research", account),
                (f"{prefix}.validation", validation),
                (f"{prefix}.person.0.research", first_person),
                (f"{prefix}.person.1.research", second_person),
                (f"{prefix}.opportunity.0.research", first_opportunity),
                (f"{prefix}.opportunity.1.research", second_opportunity),
            ):
                persist_stage_result(
                    session,
                    job_id=current.job_id,
                    claim_token=current.claim_token,
                    workflow_id=workflow.workflow_id,
                    stage_key=stage_key,
                    input_hash=workflow.input_hash,
                    outcome="complete",
                    payload=result.model_dump(mode="json"),
                    now=datetime.now(UTC),
                )
        requeue(fixture)
        assert await worker_for(fixture).run_once()
        created_kinds = [kind for kind, _ in remote.creates]
        assert created_kinds == [
            "company",
            "malgMembership",
            "person",
            "person",
            "opportunity",
            "note",
            "noteTarget",
            "opportunity",
            "note",
            "noteTarget",
        ]
        with fixture[1]() as session:
            job = session.get(ResearchJob, fixture[6].job_id)
            assert job.status == "succeeded"
            assert job.result_outcome == "complete"
            publication = session.scalar(
                select(ResearchStageResult).where(
                    ResearchStageResult.stage_key == "account.publication"
                )
            )
            assert publication.payload["target_counts"] == {
                "companies": 1,
                "people": 2,
                "opportunities": 2,
            }
            assert publication.payload["achieved_counts"] == publication.payload["target_counts"]
            assert publication.payload["candidate_attempts"] == rejected_candidates + 1
            assert publication.payload["reason_code"] is None
            progress = session.scalars(
                select(ResearchStageResult)
                .where(ResearchStageResult.stage_key == "account.progress")
                .order_by(ResearchStageResult.revision)
            ).all()
            assert progress[-1].payload["achieved_counts"] == publication.payload["target_counts"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "code",
    [
        "invalid_evidence_quote",
        "invalid_evidence_reference",
        "model_reported_budget_exhausted",
        "model_budget_with_data",
        "uncertain_invitation",
        "search_workflow_limit",
        "stage_deadline_exhausted",
        "search_agent_limit",
        "fetch_stage_limit",
        "reasoning_stage_limit",
        "no_progress_stage_limit",
        "context_stage_limit",
    ],
)
def test_account_rejected_and_budget_checkpoints_finish_without_publication(
    setup_worker, code
) -> None:
    """Committed rejected candidates are resumed and exhausted without failing the job."""

    async def run() -> None:
        campaign_id, icp_id = uuid4(), uuid4()
        fixture = setup_worker(AccountResearchJobRequest(icp_id=icp_id))
        fixture[2].add(
            "malgCampaign", {"name": "Campaign", "objective": "Research"}, str(campaign_id)
        )
        fixture[2].add(
            "malgIcp",
            {
                "name": "ICP",
                "campaignId": str(campaign_id),
                "sector": "Manufacturing",
                "geography": "Germany",
                "buyerRole": "Operations",
                "workflow": "Research",
            },
            str(icp_id),
        )
        result = AccountResearchResult(
            outcome="budget_exhausted"
            if "budget" in code or code.endswith("_limit") or code == "stage_deadline_exhausted"
            else "needs_review"
            if code == "uncertain_invitation"
            else "insufficient_evidence",
            qualification="accepted" if code == "model_budget_with_data" else "needs_review",
            unknowns=[code],
            data=AccountData(name="Review company")
            if code in {"uncertain_invitation", "model_budget_with_data"}
            else None,
            identity=AccountIdentity(
                display_name="Review company", official_website="https://example.com"
            )
            if code in {"uncertain_invitation", "model_budget_with_data"}
            else None,
        )
        workflow_id = await save_checkpoint(fixture, result)
        with fixture[1].begin() as session:
            job = session.get(ResearchJob, fixture[6].job_id)
            job = claim_next_job(session, "checkpoint-worker", datetime.now(UTC), 60, 3)
            workflow = session.get(ResearchWorkflow, workflow_id)
            for index in range(
                worker_for(fixture).research_config.max_account_candidates_per_company
            ):
                stage_payload = result.model_dump(mode="json")
                if code.endswith("_limit") or code == "stage_deadline_exhausted":
                    stage_payload["budget"] = {
                        "reason_code": code,
                        "kind": "search",
                        "used": 9,
                        "limit": 9,
                    }
                persist_stage_result(
                    session,
                    job_id=job.job_id,
                    claim_token=job.claim_token,
                    workflow_id=workflow_id,
                    stage_key=f"account.candidate.{index}.research",
                    input_hash=workflow.input_hash,
                    outcome=result.outcome,
                    payload=stage_payload,
                    now=datetime.now(UTC),
                    unknowns=[code],
                )
            fail_job(session, job.job_id, job.claim_token, "checkpoint restart", datetime.now(UTC))
            retry_failed_job(session, job.job_id, datetime.now(UTC))
        assert await worker_for(fixture).run_once()
        with fixture[1]() as session:
            job = session.get(ResearchJob, fixture[6].job_id)
            assert job.status == "succeeded"
            assert job.result_outcome == (
                "budget_exhausted"
                if code == "search_workflow_limit"
                else "needs_review"
                if code in {"uncertain_invitation", "model_budget_with_data"}
                else "insufficient_evidence"
            )
            publication = session.scalar(
                select(ResearchStageResult).where(
                    ResearchStageResult.stage_key == "account.publication",
                )
            )
            assert publication.reason_code == (
                code if code == "search_workflow_limit" else "candidate_limit_reached"
            )
            if code == "search_workflow_limit":
                assert publication.payload["budget"]["used"] == 9
                assert publication.payload["budget"]["limit"] == 9
                assert publication.payload["budget"]["stage_key"] == "account.candidate.0.research"
                assert publication.payload["candidate_attempts"] == 1
            else:
                assert publication.payload["candidate_attempts"] == 10
                assert len(publication.payload["candidate_results"]) == 10
                assert bool(publication.payload["review_candidates"]) == (
                    code in {"uncertain_invitation", "model_budget_with_data"}
                )
                if code == "model_reported_budget_exhausted":
                    assert publication.payload["candidate_results"][0]["reason_code"] == code
            assert job.failure_detail is None
            assert (
                len(
                    list(
                        session.scalars(
                            select(ResearchStageResult).where(
                                ResearchStageResult.stage_key.like("account.candidate.%")
                            )
                        )
                    )
                )
                == 10
            )
        assert not fixture[2].creates

    asyncio.run(run())


def test_provider_outage_pauses_claims_and_expired_cooldown_allows_resume(setup_worker) -> None:
    """Queued jobs wait through an outage, then resume their existing durable work."""

    async def run():
        fixture = setup_worker()
        result = ResearchResult[CampaignData](
            outcome="complete",
            data=CampaignData(name="Observed campaign", objective="Observed workflow"),
        )
        workflow_id = await save_checkpoint(fixture, result)
        with fixture[1].begin() as session:
            job = claim_next_job(session, "health-worker", datetime.now(UTC), 60, 3)
            health = persist_stage_result(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                workflow_id=workflow_id,
                stage_key="campaign.research.search_health",
                input_hash="outage",
                outcome="needs_review",
                payload={"health": "unavailable"},
                reason_code="search_unavailable",
                now=datetime.now(UTC),
            )
            health_id = health.stage_result_id
            fail_job(session, job.job_id, job.claim_token, "search_unavailable", datetime.now(UTC))
            retry_failed_job(session, job.job_id, datetime.now(UTC))
        worker = worker_for(fixture)
        assert not await worker.run_once()
        assert not fixture[2].creates
        with fixture[1].begin() as session:
            assert session.get(ResearchJob, fixture[6].job_id).status == "queued"
            session.get(ResearchStageResult, health_id).created_at = datetime.now(UTC) - timedelta(
                seconds=worker.research_config.search_unavailable_retry_seconds + 1
            )
        assert await worker.run_once()
        with fixture[1]() as session:
            assert session.get(ResearchJob, fixture[6].job_id).result_outcome == "complete"

    asyncio.run(run())
