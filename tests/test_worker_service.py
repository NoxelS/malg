"""Worker outcomes across durable checkpoints, live scope changes and cancellation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from twenty_fake import publication_fixture

from malg.config import WorkerConfig
from malg.core.models.account import AccountData, AccountIdentity, AccountResearchResult
from malg.core.models.campaign import CampaignData
from malg.core.models.icp import ICPData
from malg.core.models.jobs import (
    AccountHydrationJobRequest,
    CampaignResearchJobRequest,
    ICPResearchJobRequest,
    PersonHydrationJobRequest,
    PersonResearchJobRequest,
)
from malg.core.models.person import PersonData
from malg.core.models.research import ResearchResult
from malg.database.jobs import cancel_job, claim_next_job, enqueue_job, fail_job, retry_failed_job
from malg.database.models import (
    CrmWriteOperation,
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


async def save_checkpoint(fixture, result, *, missing=None) -> str:
    """Persist an already-validated research checkpoint before simulating a crash."""
    sessions, client, job = fixture[1], fixture[4], fixture[6]
    records = await client.read_scope(job.request_payload)
    payload = {
        "request": job.request_payload,
        "crm_inputs": {key: value.targeting_snapshot() for key, value in records.items()},
        "observed_records": {key: value.model_dump(mode="json") for key, value in records.items()},
    }
    if missing is not None:
        payload["missing_fields"] = missing
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
            stage_key=f"{job.kind}.research",
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


def test_account_hydration_reuses_original_version_and_preserves_zero(setup_worker) -> None:
    async def run() -> None:
        company_id = uuid4()
        fixture = setup_worker(AccountHydrationJobRequest(account_id=company_id))
        remote = fixture[2]
        remote.add(
            "company",
            {
                "name": "Saved Company",
                "domainName": {"primaryLinkUrl": "proof.example"},
                "malgEmployees": 0,
                "annualRevenue": {"amountMicros": "0", "currencyCode": "USD"},
                "linkedinLink": {"primaryLinkUrl": "https://linkedin.com/company/proof/"},
            },
            str(company_id),
        )
        original_version = remote.records["company", str(company_id)]["updatedAt"]
        result = AccountResearchResult(
            outcome="complete",
            data=AccountData(
                name="Saved Company",
                sector="Observed sector",
                employees=0,
                website="https://proof.example",
            ),
            identity=AccountIdentity(
                display_name="Saved Company", official_website="https://proof.example"
            ),
            qualification="accepted",
        )
        await save_checkpoint(fixture, result, missing=["sector"])
        remote.edit("company", str(company_id), {})
        await worker_for(fixture).run_once()
        assert remote.records["company", str(company_id)]["malgEmployees"] == 0
        assert remote.records["company", str(company_id)]["malgSector"] == "Observed sector"
        with fixture[1]() as session:
            job = session.get(ResearchJob, fixture[6].job_id)
            assert job.status == "succeeded"
            assert job.result_outcome == "complete"
            operation = session.scalar(select(CrmWriteOperation))
            assert operation.observed_record_version == original_version
            keys = set(session.scalars(select(ResearchStageResult.stage_key)))
            assert {
                "account_hydration.observation",
                "account_hydration.research",
                "account_hydration.proposals",
                "account_hydration.publication",
            } <= keys

    asyncio.run(run())


def test_person_research_completes_native_company_link_and_stage_history(setup_worker) -> None:
    async def run() -> None:
        company_id, icp_id, campaign_id = uuid4(), uuid4(), uuid4()
        fixture = setup_worker(PersonResearchJobRequest(account_id=company_id, icp_id=icp_id))
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
        remote.add(
            "company",
            {"name": "Saved Company", "domainName": {"primaryLinkUrl": "proof.example"}},
            str(company_id),
        )
        remote.add(
            "malgMembership",
            {"name": "Membership", "companyId": str(company_id), "icpId": str(icp_id)},
        )
        await save_checkpoint(
            fixture,
            ResearchResult[PersonData](
                outcome="complete",
                data=PersonData(first_name="Ada", last_name="Lovelace", job_title="Founder"),
            ),
        )
        await worker_for(fixture).run_once()
        assert len(remote.creates) == 1
        kind, person_id = remote.creates[0]
        assert kind == "person"
        assert remote.records[kind, person_id]["companyId"] == str(company_id)
        with fixture[1]() as session:
            job = session.get(ResearchJob, fixture[6].job_id)
            assert job.status == "succeeded"
            assert job.result_outcome == "complete"
            assert job.result_refs[0]["record_id"] == person_id
            assert set(session.scalars(select(ResearchStageResult.stage_key))) == {
                "person.research",
                "person.publication",
            }

    asyncio.run(run())


def test_person_without_company_finishes_review_without_generation_or_writes(setup_worker) -> None:
    fixture = setup_worker(PersonHydrationJobRequest(person_id=uuid4()))
    fixture[2].add("person", {"name": {"firstName": "Mononym"}}, fixture[6].person_id)
    requeue(fixture)
    asyncio.run(worker_for(fixture).run_once())
    with fixture[1]() as session:
        job = session.get(ResearchJob, fixture[6].job_id)
        assert job.status == "succeeded"
        assert job.result_outcome == "needs_review"
    assert fixture[2].creates == []
    assert fixture[2].updates == 0


def test_worker_cancels_waiting_remote_operation_when_claim_is_revoked(setup_worker) -> None:
    async def run() -> None:
        company_id = uuid4()
        fixture = setup_worker(AccountHydrationJobRequest(account_id=company_id))
        remote = fixture[2]
        remote.add(
            "company",
            {"name": "Saved Company", "domainName": {"primaryLinkUrl": "proof.example"}},
            str(company_id),
        )
        result = AccountResearchResult(
            outcome="partial",
            data=AccountData(
                name="Saved Company", sector="Observed", website="https://proof.example"
            ),
            identity=AccountIdentity(
                display_name="Saved Company", official_website="https://proof.example"
            ),
            qualification="accepted",
        )
        await save_checkpoint(
            fixture, result, missing=["sector", "employees", "annual_revenue", "linkedin_url"]
        )
        issued = asyncio.Event()

        async def pause_transport():
            issued.set()
            await asyncio.Event().wait()

        remote.before_fill = pause_transport
        execution = asyncio.create_task(worker_for(fixture).run_once())
        await asyncio.wait_for(issued.wait(), 3)
        with fixture[1].begin() as session:
            cancel_job(session, fixture[6].job_id, datetime.now(UTC))
        assert await asyncio.wait_for(execution, 3)
        with fixture[1]() as session:
            job = session.get(ResearchJob, fixture[6].job_id)
            assert job.status == "cancelled"
            assert job.result_outcome is None
            assert session.get(ResearchWorkflow, job.workflow_id).status == "cancelled"
            assert session.scalar(select(CrmWriteOperation)).status == "prepared"
        assert remote.updates == 0

    asyncio.run(run())
