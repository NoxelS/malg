"""Observable replay, cancellation and atomic-hydration behavior through real HTTP adapters."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from twenty_fake import publication_fixture

from malg.core.models.account import (
    AccountData,
    AccountIdentity,
    AccountResearchResult,
    AccountValidationAssessment,
    AccountValidationOutcome,
    CheckOutcome,
    ValidationCheck,
)
from malg.core.models.campaign import CampaignData
from malg.crm.client import TwentyError, TwentyRecordMissing
from malg.database.crm_writes import CrmWriteConflict, prepare_write
from malg.database.jobs import cancel_job
from malg.database.models import CrmWriteOperation, ResearchJob


@pytest.fixture
def publication(twenty_metadata):
    """Release real persistence and HTTP resources after each isolated scenario."""
    fixture = publication_fixture(twenty_metadata)
    yield fixture
    asyncio.run(fixture[3].aclose())
    fixture[0].dispose()


def account_result() -> AccountResearchResult:
    """Represent already host-validated research at the publication boundary."""
    return AccountResearchResult(
        data=AccountData(name="Proof Company", website="https://proof.example", employees=0),
        identity=AccountIdentity(
            display_name="Proof Company", official_website="https://proof.example"
        ),
        qualification="accepted",
        outcome="complete",
    )


def accepted_validation() -> AccountValidationAssessment:
    """Represent an independent accepted disposition, not an agent stub."""
    return AccountValidationAssessment(
        outcome="accepted",
        rationale="Observed company fits the requested scope.",
        checks=[
            ValidationCheck(
                check_type="identity",
                target="proof.example",
                outcome=CheckOutcome.PASS,
                reason="Independent identity evidence agrees.",
            )
        ],
        validated_at=datetime.now(UTC),
    )


def test_response_loss_replay_preserves_human_edit_and_never_recreates_deleted_record(
    publication,
) -> None:
    async def run() -> None:
        _, sessions, remote, _, _, publisher, job = publication
        remote.lose_response.add("malgCampaign")
        data = CampaignData(name="Proof", objective="Observed operational need")
        identifier = await publisher.publish_campaign(job.job_id, data, claim_token=job.claim_token)
        remote.edit("malgCampaign", identifier, {"name": "Human edit"})
        assert (
            await publisher.publish_campaign(job.job_id, data, claim_token=job.claim_token)
            == identifier
        )
        assert remote.records["malgCampaign", identifier]["name"] == "Human edit"
        assert len(remote.creates) == 1
        with sessions() as session:
            operation = session.scalar(select(CrmWriteOperation))
            assert operation.status == "confirmed"
            assert operation.attempt_count == 1
        del remote.records["malgCampaign", identifier]
        with pytest.raises(TwentyRecordMissing):
            await publisher.publish_campaign(job.job_id, data, claim_token=job.claim_token)
        assert len(remote.creates) == 1

    asyncio.run(run())


def test_changed_payload_or_contract_cannot_inherit_confirmation(publication) -> None:
    async def run() -> None:
        _, sessions, remote, _, _, publisher, job = publication
        data = CampaignData(name="Proof", objective="Original intended objective")
        identifier = await publisher.publish_campaign(job.job_id, data, claim_token=job.claim_token)
        with pytest.raises(CrmWriteConflict):
            await publisher.publish_campaign(
                job.job_id,
                CampaignData(name="Changed", objective=data.objective),
                claim_token=job.claim_token,
            )
        with sessions.begin() as session:
            operation = session.scalar(select(CrmWriteOperation))
            with pytest.raises(CrmWriteConflict):
                prepare_write(
                    session,
                    job_id=job.job_id,
                    workflow_id=operation.workflow_id,
                    stage_key=operation.stage_key,
                    object_name=operation.object_name,
                    record_id=identifier,
                    field_key=operation.field_key,
                    contract_version=operation.contract_version,
                    contract_hash="different-contract",
                    intended_fields=operation.intended_fields,
                    observed_record_version=None,
                    claim_token=job.claim_token,
                )
        assert remote.records["malgCampaign", identifier]["name"] == "Proof"
        assert len(remote.creates) == 1

    asyncio.run(run())


def test_cancellation_during_issued_request_reconciles_without_stale_completion(
    publication,
) -> None:
    async def run() -> None:
        _, sessions, remote, _, _, publisher, job = publication

        def cancel_in_flight() -> None:
            with sessions.begin() as session:
                cancel_job(session, job.job_id, datetime.now(UTC))

        remote.after_create = cancel_in_flight
        with pytest.raises(ValueError, match="claim"):
            await publisher.publish_campaign(
                job.job_id,
                CampaignData(name="Proof", objective="Cancellation boundary"),
                claim_token=job.claim_token,
            )
        with sessions() as session:
            operation = session.scalar(select(CrmWriteOperation))
            assert operation.status == "prepared"
            assert operation.attempt_count == 1
            assert session.get(ResearchJob, job.job_id).result_refs == []
        await publisher.reconcile_pending()
        with sessions() as session:
            stored = session.get(ResearchJob, job.job_id)
            assert stored.status == "cancelled"
            assert stored.claim_token is None
            assert stored.result_refs[0]["record_id"] == remote.creates[0][1]
            assert session.scalar(select(CrmWriteOperation)).status == "confirmed"
        with pytest.raises(ValueError, match="claim"):
            await publisher.publish_campaign(
                job.job_id,
                CampaignData(name="Other", objective="No new effects"),
                claim_token=job.claim_token,
            )
        assert len(remote.creates) == 1

    asyncio.run(run())


def test_company_success_membership_failure_resumes_without_overwriting_company(
    publication,
) -> None:
    async def run() -> None:
        _, sessions, remote, _, _, publisher, job = publication
        remote.reject_once.add("malgMembership")
        icp_id = str(uuid4())
        result, validation = account_result(), accepted_validation()
        with pytest.raises(TwentyError):
            await publisher.publish_account(
                job.job_id, result, validation, icp_id=icp_id, claim_token=job.claim_token
            )
        company_id = remote.creates[0][1]
        assert remote.records["company", company_id]["malgEmployees"] == 0
        with sessions() as session:
            assert session.get(ResearchJob, job.job_id).result_refs[0]["record_id"] == company_id
        remote.edit("company", company_id, {"name": "Human-owned company name"})
        resumed_company, membership_id, reason = await publisher.publish_account(
            job.job_id, result, validation, icp_id=icp_id, claim_token=job.claim_token
        )
        assert reason is None
        assert resumed_company == company_id
        assert remote.records["company", company_id]["name"] == "Human-owned company name"
        assert remote.records["malgMembership", membership_id]["companyId"] == company_id
        assert remote.records["malgMembership", membership_id]["icpId"] == icp_id
        assert [kind for kind, _ in remote.creates] == ["company", "malgMembership"]

    asyncio.run(run())


def test_independent_rejection_and_conflicting_company_identity_publish_nothing(
    publication,
) -> None:
    async def run() -> None:
        _, _, remote, _, _, publisher, job = publication
        validation = accepted_validation().model_copy(
            update={"outcome": AccountValidationOutcome.REJECTED}
        )
        assert (
            await publisher.publish_account(
                job.job_id,
                account_result(),
                validation,
                icp_id=str(uuid4()),
                claim_token=job.claim_token,
            )
        )[2] == "account_not_qualified"
        assert remote.records == {}
        remote.add("company", {"name": "First", "domainName": {"primaryLinkUrl": "proof.example"}})
        remote.add("company", {"name": "Second", "domainName": {"primaryLinkUrl": "proof.example"}})
        _, _, reason = await publisher.publish_account(
            job.job_id,
            account_result(),
            accepted_validation(),
            icp_id=str(uuid4()),
            claim_token=job.claim_token,
        )
        assert reason == "multiple_identity_matches"
        assert remote.creates == []

    asyncio.run(run())


def test_concurrent_human_value_wins_atomic_hydration_race(publication) -> None:
    async def run() -> None:
        _, sessions, remote, _, _, publisher, job = publication
        identifier = remote.add(
            "company", {"name": "Proof", "domainName": {"primaryLinkUrl": "proof.example"}}
        )
        original = remote.records["company", identifier]["updatedAt"]
        issued, edited = asyncio.Event(), asyncio.Event()

        async def pause_transport() -> None:
            issued.set()
            await edited.wait()

        async def human_edit() -> None:
            await issued.wait()
            remote.edit("company", identifier, {"malgEmployees": 42})
            edited.set()

        remote.before_fill = pause_transport
        outcomes, _ = await asyncio.gather(
            publisher.hydrate(
                job.job_id,
                "company",
                identifier,
                original,
                {"malgEmployees": 50},
                claim_token=job.claim_token,
            ),
            human_edit(),
        )
        assert remote.records["company", identifier]["malgEmployees"] == 42
        assert outcomes[0]["status"] == "conflict"
        with sessions() as session:
            operation = session.scalar(select(CrmWriteOperation))
            assert operation.observed_record_version == original
            assert operation.attempt_count == 1

    asyncio.run(run())


def test_hydration_preserves_zero_secondary_contacts_and_fullname_sibling(publication) -> None:
    async def run() -> None:
        _, _, remote, _, _, publisher, job = publication
        company_id = remote.add("company", {"malgEmployees": 0})
        version = remote.records["company", company_id]["updatedAt"]
        outcome = await publisher.hydrate(
            job.job_id,
            "company",
            company_id,
            version,
            {"malgEmployees": 50},
            claim_token=job.claim_token,
        )
        assert outcome[0]["status"] == "conflict"
        assert remote.records["company", company_id]["malgEmployees"] == 0
        person_id = remote.add(
            "person",
            {
                "name": {"firstName": "Ada"},
                "companyId": company_id,
                "emails": {"additionalEmails": ["preserved@example.test"]},
                "linkedinLink": {
                    "secondaryLinks": [{"label": "saved", "url": "https://linkedin.com/in/saved/"}]
                },
            },
        )
        version = remote.records["person", person_id]["updatedAt"]
        outcomes = await publisher.hydrate(
            job.job_id,
            "person",
            person_id,
            version,
            {
                "name": {"lastName": "Lovelace"},
                "emails": {"primaryEmail": "new@example.test"},
                "linkedinLink": {"primaryLinkUrl": "https://linkedin.com/in/new/"},
            },
            claim_token=job.claim_token,
        )
        assert [item["status"] for item in outcomes] == ["confirmed", "conflict", "conflict"]
        record = remote.records["person", person_id]
        assert record["name"] == {"firstName": "Ada", "lastName": "Lovelace"}
        assert record["emails"]["additionalEmails"] == ["preserved@example.test"]
        assert record["emails"]["primaryEmail"] == ""
        assert remote.updates == 1

    asyncio.run(run())


def test_person_identity_matches_never_reparent_and_no_match_permits_creation(publication) -> None:
    async def run() -> None:
        _, _, remote, _, _, publisher, job = publication
        company_id, other_company = str(uuid4()), str(uuid4())
        identifier = remote.add(
            "person",
            {
                "name": {"firstName": "Ada"},
                "companyId": other_company,
                "emails": {"primaryEmail": "ada@example.test"},
            },
        )
        matched, reason = await publisher.match_person(
            job.job_id,
            company_id=company_id,
            email="ada@example.test",
            first_name="Ada",
            claim_token=job.claim_token,
        )
        assert matched is None
        assert reason == "crm_identity_conflict"
        assert remote.records["person", identifier]["companyId"] == other_company
        assert await publisher.match_person(
            job.job_id, company_id=company_id, first_name="Different", claim_token=job.claim_token
        ) == (None, None)

    asyncio.run(run())


def test_secondary_company_identity_reuses_native_record_without_overwriting_it(
    publication,
) -> None:
    async def run() -> None:
        _, _, remote, _, _, publisher, job = publication
        existing = remote.add(
            "company",
            {
                "name": "Human label",
                "domainName": {
                    "primaryLinkUrl": "https://other.example",
                    "secondaryLinks": [
                        {"label": "Official alias", "url": "https://WWW.PROOF.EXAMPLE/about"}
                    ],
                },
            },
        )
        remote.add(
            "company", {"domainName": {"primaryLinkUrl": "https://proof.example.attacker.test"}}
        )
        identifier, membership, reason = await publisher.publish_account(
            job.job_id,
            account_result(),
            accepted_validation(),
            icp_id=str(uuid4()),
            claim_token=job.claim_token,
        )
        assert (identifier, reason) == (existing, None)
        assert membership is not None
        assert remote.records["company", existing]["name"] == "Human label"
        assert all(kind != "company" for kind, _ in remote.creates)

    asyncio.run(run())


def test_person_identity_normalizes_additional_email_and_company_scoped_full_name(
    publication,
) -> None:
    async def run() -> None:
        _, _, remote, _, client, _, _ = publication
        company = str(uuid4())
        person = remote.add(
            "person",
            {
                "companyId": company,
                "name": {"firstName": "  Ada ", "lastName": "LOVELACE"},
                "emails": {
                    "primaryEmail": "different@example.test",
                    "additionalEmails": ["ADA@EXAMPLE.TEST"],
                },
            },
        )
        remote.add(
            "person",
            {"companyId": str(uuid4()), "name": {"firstName": "Ada", "lastName": "Lovelace"}},
        )
        by_email = await client.find_people(company_id=str(uuid4()), email="ada@example.test")
        by_name = await client.find_people(
            company_id=company, first_name="Ada", last_name="Lovelace"
        )
        assert [row["id"] for row in by_email] == [person]
        assert [row["id"] for row in by_name] == [person]

    asyncio.run(run())
