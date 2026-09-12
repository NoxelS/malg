"""Observable API outcomes for persisted campaign and ICP artifacts."""

from __future__ import annotations

import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from tests.fixtures import _icp, authenticated_client
from tests.test_campaign_research_agent import campaign_payload

from malg.core.models.jobs import ResearchJobStatus
from malg.database import Base
from malg.database.models import ResearchJob


def _engine() -> Engine:
    """Create an isolated SQLite engine with foreign keys enabled for endpoint tests."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: sqlite3.Connection, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    return engine


def test_campaign_and_icp_crud_are_scoped_and_validated() -> None:
    """Create, replace, list, and delete the two canonical artifact types."""
    engine = _engine()
    client = authenticated_client(engine)
    campaign = campaign_payload()
    icp = _icp("manufacturing-ops", "Incident intake").model_dump(mode="json")

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    assert client.post("/api/v1/campaigns", json=campaign).status_code == 201
    assert client.post("/api/v1/campaigns", json=campaign).status_code == 409
    assert client.get("/api/v1/campaigns").json() == [campaign]

    mismatched_icp = deepcopy(icp)
    mismatched_icp["campaign_id"] = "another-campaign"
    assert (
        client.post(
            f"/api/v1/campaigns/{campaign['campaign_id']}/icps", json=mismatched_icp
        ).status_code
        == 422
    )
    assert (
        client.post(f"/api/v1/campaigns/{campaign['campaign_id']}/icps", json=icp).status_code
        == 201
    )
    assert client.get(f"/api/v1/campaigns/{campaign['campaign_id']}/icps").json() == [icp]

    replaced = deepcopy(icp)
    replaced["title"] = "Updated manufacturing operations ICP"
    assert (
        client.put(
            f"/api/v1/campaigns/{campaign['campaign_id']}/icps/{icp['icp_id']}", json=replaced
        ).json()
        == replaced
    )
    assert client.delete(f"/api/v1/campaigns/{campaign['campaign_id']}").status_code == 204
    assert client.get(f"/api/v1/campaigns/{campaign['campaign_id']}").status_code == 404
    assert client.get(f"/api/v1/campaigns/{campaign['campaign_id']}/icps").status_code == 404


def test_campaign_research_batch_is_bounded_and_persisted() -> None:
    """Queue valid campaign batches and reject invalid amounts atomically."""
    engine = _engine()
    client = authenticated_client(engine)

    response = client.post("/api/v1/jobs/campaigns", json={"amount": 3})
    assert response.status_code == 202
    records = response.json()
    assert len(records) == 3
    assert len({record["job_id"] for record in records}) == 3
    assert all(record["kind"] == "campaign" for record in records)
    assert all(record["status"] == "queued" for record in records)
    assert all(record["attempt_count"] == 0 for record in records)

    queued = client.get("/api/v1/jobs", params={"status": "queued"})
    assert queued.status_code == 200
    assert {record["job_id"] for record in queued.json()} == {
        record["job_id"] for record in records
    }

    for amount in (0, 101, 1.5, True):
        invalid = client.post("/api/v1/jobs/campaigns", json={"amount": amount})
        assert invalid.status_code == 422
    assert len(client.get("/api/v1/jobs", params={"status": "queued"}).json()) == 3


def test_generic_jobs_validate_and_persist_selected_scopes() -> None:
    """Queue ICP and account jobs only for existing parent scopes."""
    engine = _engine()
    client = authenticated_client(engine)
    campaign = campaign_payload()
    icp = _icp("manufacturing-ops", "Incident intake").model_dump(mode="json")

    assert client.post("/api/v1/campaigns", json=campaign).status_code == 201
    assert (
        client.post(f"/api/v1/campaigns/{campaign['campaign_id']}/icps", json=icp).status_code
        == 201
    )

    icp_job = client.post(
        "/api/v1/jobs",
        json={"kind": "icp", "campaign_id": campaign["campaign_id"]},
    )
    assert icp_job.status_code == 202
    assert icp_job.json()["status"] == "queued"
    assert icp_job.json()["kind"] == "icp"
    assert {key: icp_job.json()[key] for key in ("campaign_id", "icp_id")} == {
        "campaign_id": campaign["campaign_id"],
        "icp_id": None,
    }

    account_job = client.post(
        "/api/v1/jobs",
        json={
            "kind": "account",
            "campaign_id": campaign["campaign_id"],
            "icp_id": icp["icp_id"],
        },
    )
    assert account_job.status_code == 202
    assert account_job.json()["status"] == "queued"
    assert account_job.json()["kind"] == "account"
    assert {key: account_job.json()[key] for key in ("campaign_id", "icp_id")} == {
        "campaign_id": campaign["campaign_id"],
        "icp_id": icp["icp_id"],
    }

    assert (
        client.post(
            "/api/v1/jobs",
            json={"kind": "icp", "campaign_id": "unknown-campaign"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/jobs",
            json={
                "kind": "account",
                "campaign_id": "wrong-campaign",
                "icp_id": icp["icp_id"],
            },
        ).status_code
        == 404
    )
    assert len(client.get("/api/v1/jobs", params={"status": "queued"}).json()) == 2


def test_jobs_overview_lists_all_lifecycle_records_and_cancels_queued_only() -> None:
    """The jobs overview preserves durable records and the queued-only action boundary."""
    engine = _engine()
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as connection:
        from sqlalchemy.orm import Session

        with Session(connection) as session:
            for status in ResearchJobStatus:
                session.add(
                    ResearchJob(
                        job_id=f"{status.value}-job",
                        kind="campaign",
                        status=status.value,
                        attempt_count=1,
                        created_at=created_at,
                        started_at=created_at if status != ResearchJobStatus.QUEUED else None,
                        finished_at=created_at
                        if status
                        in {
                            ResearchJobStatus.SUCCEEDED,
                            ResearchJobStatus.FAILED,
                            ResearchJobStatus.CANCELLED,
                        }
                        else None,
                        failure_detail="persisted failure"
                        if status == ResearchJobStatus.FAILED
                        else None,
                    )
                )
            session.commit()
    client = authenticated_client(engine)
    listed = client.get("/api/v1/jobs")
    assert listed.status_code == 200
    records = {record["status"]: record for record in listed.json()}
    assert set(records) == {status.value for status in ResearchJobStatus}
    assert records["failed"]["failure_detail"] == "persisted failure"
    assert records["succeeded"]["finished_at"] is not None

    for job_id in ("succeeded-job", "failed-job", "cancelled-job"):
        deleted = client.delete(f"/api/v1/jobs/{job_id}")
        assert deleted.status_code == 204
    remaining = client.get("/api/v1/jobs")
    assert remaining.status_code == 200
    assert {record["status"] for record in remaining.json()} == {"queued", "running"}
    assert client.delete("/api/v1/jobs/queued-job").status_code == 409
    assert client.delete("/api/v1/jobs/running-job").status_code == 409
    assert client.delete("/api/v1/jobs/missing-job").status_code == 404

    listed = client.get("/api/v1/jobs")
    assert {record["status"] for record in listed.json()} == {"queued", "running"}

    cancelled = client.post("/api/v1/jobs/queued-job/cancel", json={})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.post("/api/v1/jobs/queued-job/cancel", json={}).status_code == 409
    assert client.post("/api/v1/jobs/running-job/cancel", json={}).status_code == 409
