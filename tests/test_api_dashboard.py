"""Observable read-only dashboard API outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from malg.api.app import create_app
from malg.database.models import ICP, Account, Base, Campaign, ResearchJob, WorkerHeartbeat


def test_dashboard_reports_all_statuses_and_current_worker_claims() -> None:
    """Dashboard counts all lifecycle states and excludes stale workers."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.add(Campaign(campaign_id="campaign", title="Campaign", payload={}))
        session.add(
            ICP(
                campaign_id="campaign",
                icp_id="icp",
                segment_key="segment",
                title="ICP",
                payload={},
            )
        )
        session.add(
            Account(
                account_id="account",
                identity_key="account-key",
                display_name="Account",
                payload={},
            )
        )
        for status in ("queued", "running", "succeeded", "failed", "cancelled"):
            session.add(
                ResearchJob(
                    job_id=f"{status}-job",
                    kind="campaign",
                    status=status,
                    attempt_count=1 if status == "running" else 0,
                    claim_token="running-worker" if status == "running" else None,
                    claimed_at=now if status == "running" else None,
                )
            )
        session.add(WorkerHeartbeat(worker_token="idle-worker", last_seen_at=now))
        session.add(WorkerHeartbeat(worker_token="running-worker", last_seen_at=now))
        session.add(
            WorkerHeartbeat(worker_token="stale-worker", last_seen_at=now - timedelta(seconds=30))
        )

    client = TestClient(create_app(database_engine=engine))
    assert client.get("/api/v1/dashboard").json() == {
        "active_workers": 2,
        "campaigns": 1,
        "icps": 1,
        "accounts": 1,
        "jobs": {"queued": 1, "running": 1, "succeeded": 1, "failed": 1, "cancelled": 1},
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
