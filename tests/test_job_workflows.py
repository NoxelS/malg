"""Behavioral contracts for durable research-workflow persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from malg.core.models.jobs import CampaignResearchJobRequest
from malg.database.crm_writes import prepare_write, reconcile_write
from malg.database.jobs import cancel_job, claim_next_job, delete_job, enqueue_job
from malg.database.models import Base, ResearchJob
from malg.database.research import create_workflow_for_job


def test_retry_reuses_the_original_workflow_input_snapshot() -> None:
    """A retry keeps its workflow identity and immutable original input."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    initial_deadline = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=3)

    with sessions.begin() as session:
        job = enqueue_job(CampaignResearchJobRequest(), session)
        first = create_workflow_for_job(session, job, {"objective": "original"}, initial_deadline)
        second = create_workflow_for_job(
            session,
            job,
            {"objective": "must not replace the original snapshot"},
            initial_deadline + timedelta(minutes=3),
        )

        assert second.workflow_id == first.workflow_id
        assert second.input_payload == {"objective": "original"}
        assert second.input_hash == first.input_hash
        assert second.deadline_at == initial_deadline


def test_job_with_unresolved_remote_write_cannot_be_deleted() -> None:
    """A terminal job remains available until its remote intent is reconciled."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)

    with sessions.begin() as session:
        job = enqueue_job(CampaignResearchJobRequest(), session)
        claimed = claim_next_job(session, "worker", datetime.now(UTC), 60, 3)
        assert claimed is not None
        operation = prepare_write(
            session,
            job_id=job.job_id,
            workflow_id=None,
            stage_key="campaign.publication",
            object_name="malgCampaign",
            record_id=job.job_id,
            field_key="$create",
            contract_version=1,
            contract_hash="a" * 64,
            intended_fields={"name": "Campaign", "objective": "Objective"},
            observed_record_version=None,
            claim_token=claimed.claim_token,
        )
        cancel_job(session, job.job_id, datetime.now(UTC))
        assert not delete_job(session, job.job_id)

        reconcile_write(session, operation, status="confirmed")
        assert delete_job(session, job.job_id)
        assert session.get(ResearchJob, job.job_id) is None
