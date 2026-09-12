"""Observable durable worker service behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from malg.config import WorkerConfig
from malg.database.models import Base, WorkerHeartbeat
from malg.worker.service import ResearchWorker


def test_worker_heartbeat_inserts_then_updates_existing_token() -> None:
    """A worker heartbeat persists its stable process token and refreshes liveness."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    worker = ResearchWorker(
        sessions,
        WorkerConfig(
            poll_interval_seconds=1, lease_seconds=60, max_attempts=3, heartbeat_timeout_seconds=15
        ),
    )

    asyncio.run(worker.heartbeat())
    with sessions() as session:
        first = session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_token == worker.worker_token)
        )
        assert first is not None
        created_at = first.created_at
        first_seen = first.last_seen_at

    asyncio.run(worker.heartbeat())
    with sessions() as session:
        second = session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_token == worker.worker_token)
        )
        assert second is not None
        assert second.created_at == created_at
        assert second.last_seen_at >= first_seen


def test_claimed_at_is_current_claim_timestamp() -> None:
    """A retry receives a new current-claim timestamp while started_at remains stable."""
    from malg.core.models.jobs import CampaignResearchJobRequest
    from malg.database.jobs import claim_next_job

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    first_now = datetime(2026, 1, 1, tzinfo=UTC)
    second_now = first_now + timedelta(minutes=1)
    with sessions.begin() as session:
        from malg.database.jobs import enqueue_job

        job = enqueue_job(CampaignResearchJobRequest(), session)
        job_id = job.job_id
    with sessions.begin() as session:
        first = claim_next_job(session, "worker", first_now, 1, 3)
        assert first is not None
        started_at = first.started_at
        assert first.claimed_at == first_now
        first.claim_expires_at = first_now
    with sessions.begin() as session:
        second = claim_next_job(session, "worker", second_now, 1, 3)
        assert second is not None
        assert second.job_id == job_id
        assert second.claimed_at == second_now
        assert second.started_at.replace(tzinfo=UTC) == started_at


def test_reset_interrupted_jobs_only_cancels_running_claims() -> None:
    """Worker replacement cancels running work while preserving other lifecycle records."""
    from malg.core.models.jobs import ResearchJobStatus
    from malg.database.models import ResearchJob

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    restart_at = datetime(2026, 1, 2, tzinfo=UTC)
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    with sessions.begin() as session:
        jobs = [
            ResearchJob(
                job_id=status,
                kind="campaign",
                status=status,
                created_at=created_at,
                attempt_count=1 if status == ResearchJobStatus.RUNNING.value else 0,
                claim_token="old-worker" if status == ResearchJobStatus.RUNNING.value else None,
                claim_expires_at=restart_at + timedelta(hours=1)
                if status == ResearchJobStatus.RUNNING.value
                else None,
                claimed_at=restart_at if status == ResearchJobStatus.RUNNING.value else None,
                started_at=restart_at if status == ResearchJobStatus.RUNNING.value else None,
                failure_detail="persisted failure"
                if status == ResearchJobStatus.FAILED.value
                else None,
            )
            for status in ResearchJobStatus
        ]
        session.add_all(jobs)
    worker = ResearchWorker(
        sessions,
        WorkerConfig(
            poll_interval_seconds=1, lease_seconds=60, max_attempts=3, heartbeat_timeout_seconds=15
        ),
    )
    assert worker.reset_interrupted_jobs() == 1
    with sessions() as session:
        records = {job.job_id: job for job in session.scalars(select(ResearchJob))}
        interrupted = records[ResearchJobStatus.RUNNING.value]
        assert interrupted.status == ResearchJobStatus.CANCELLED.value
        assert interrupted.finished_at is not None
        assert interrupted.claim_token is None
        assert interrupted.claim_expires_at is None
        assert interrupted.claimed_at is None
        assert interrupted.attempt_count == 1
        assert records[ResearchJobStatus.QUEUED.value].finished_at is None
        assert records[ResearchJobStatus.SUCCEEDED.value].finished_at is None
        assert records[ResearchJobStatus.FAILED.value].failure_detail == "persisted failure"
