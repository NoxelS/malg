"""Observable read-only dashboard API outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tests.fixtures import authenticated_client

from malg.database.models import Base, ResearchJob, WorkerHeartbeat


def test_dashboard_reports_all_statuses_and_current_worker_claims() -> None:
    """Dashboard counts all lifecycle states and excludes stale workers."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        for status in ("queued", "running", "succeeded", "failed", "cancelled"):
            session.add(
                ResearchJob(
                    job_id=f"{status}-job",
                    kind="campaign",
                    status=status,
                    attempt_count=1 if status == "running" else 0,
                    owner_worker_token="running-worker" if status == "running" else None,
                    claim_token="running-claim" if status == "running" else None,
                    claimed_at=now if status == "running" else None,
                    result_outcome="complete" if status == "succeeded" else None,
                )
            )
        session.add(
            ResearchJob(
                job_id="campaign-fast-job",
                kind="campaign",
                status="succeeded",
                started_at=now - timedelta(seconds=30),
                finished_at=now,
                result_outcome="complete",
            )
        )
        session.add(
            ResearchJob(
                job_id="campaign-slow-job",
                kind="campaign",
                status="succeeded",
                started_at=now - timedelta(seconds=90),
                finished_at=now,
                result_outcome="partial",
            )
        )
        session.add(
            ResearchJob(
                job_id="icp-job",
                kind="icp",
                status="succeeded",
                started_at=now - timedelta(seconds=120),
                finished_at=now,
                result_outcome="budget_exhausted",
            )
        )
        session.add(WorkerHeartbeat(worker_token="idle-worker", last_seen_at=now))
        session.add(WorkerHeartbeat(worker_token="running-worker", last_seen_at=now))
        session.add(
            WorkerHeartbeat(worker_token="stale-worker", last_seen_at=now - timedelta(seconds=30))
        )

    client = authenticated_client(engine)
    assert client.get("/api/v1/dashboard").json() == {
        "active_workers": 2,
        "jobs": {"queued": 1, "running": 1, "succeeded": 4, "failed": 1, "cancelled": 1},
        "outcomes": {
            "complete": 2,
            "partial": 1,
            "needs_review": 0,
            "insufficient_evidence": 0,
            "budget_exhausted": 1,
        },
        "job_durations": [
            {"kind": "campaign", "average_duration_seconds": 60.0},
            {"kind": "icp", "average_duration_seconds": 120.0},
            {"kind": "account", "average_duration_seconds": None},
        ],
    }
    workers = client.get("/api/v1/workers").json()
    assert [worker["worker_id"] for worker in workers] == ["running-worker", "idle-worker"]
    assert workers[0]["status"] == "running"
    assert workers[0]["job"] == {
        "job_id": "running-job",
        "kind": "campaign",
        "attempt_count": 1,
        "claimed_at": workers[0]["job"]["claimed_at"],
    }
    assert workers[1]["status"] == "idle"
    assert workers[1]["job"] is None

    first_page = client.get("/api/v1/workers/overview", params={"limit": 1, "offset": 0})
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 2
    assert [worker["worker_id"] for worker in first_page.json()["items"]] == ["running-worker"]
    assert first_page.json()["limit"] == 1
    assert first_page.json()["offset"] == 0

    second_page = client.get("/api/v1/workers/overview", params={"limit": 1, "offset": 1})
    assert [worker["worker_id"] for worker in second_page.json()["items"]] == ["idle-worker"]
    assert client.get("/api/v1/workers/overview", params={"limit": 101}).status_code == 422
