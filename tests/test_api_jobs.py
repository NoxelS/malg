"""Admission observes real remote scope while history remains local."""

from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from malg.api.app import create_app
from malg.config import AuthConfig, TwentyConfig
from malg.crm.client import TwentyClient
from malg.database.models import Base, CrmWriteOperation, ResearchJob


def test_missing_remote_parent_rejects_entire_batch(twenty_metadata):
    """A human-created/missing remote Campaign is checked before local inserts."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def handler(request):
        if request.url.path == "/metadata":
            return httpx.Response(200, json=twenty_metadata)
        return httpx.Response(200, json={"data": {"malgCampaigns": {"edges": []}}})

    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://twenty.test"
    )
    crm = TwentyClient(
        TwentyConfig("https://twenty.test", "https://twenty.test", "test", "test", max_retries=0),
        transport,
    )
    with TestClient(
        create_app(
            database_engine=engine, auth_config=AuthConfig("admin", "secret"), crm_client=crm
        )
    ) as client:
        token = client.post(
            "/api/v1/auth/token", json={"username": "admin", "password": "secret"}
        ).json()["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        response = client.post("/api/v1/jobs/icps", json={"campaign_id": str(uuid4()), "amount": 3})
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "crm_record_missing"
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(ResearchJob)) == 0
        response = client.post("/api/v1/jobs", json={"kind": "campaign"})
        assert response.status_code == 202, response.text
        assert response.json()["contract_hash"]
        assert response.json()["request_payload"] == {"kind": "campaign"}
        assert (
            client.post("/api/v1/jobs", json={"kind": "campaign", "extra": True}).status_code == 422
        )
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(ResearchJob)) == 1


def test_local_job_history_and_cancellation_do_not_require_crm():
    """Local job history remains usable when Twenty is unavailable."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    job_id = str(uuid4())
    with Session(engine) as session:
        session.add(
            ResearchJob(
                job_id=job_id,
                kind="campaign",
                status="queued",
                request_payload={"kind": "campaign"},
                result_refs=[],
            )
        )
        session.commit()

    with TestClient(
        create_app(database_engine=engine, auth_config=AuthConfig("admin", "secret"))
    ) as client:
        token = client.post(
            "/api/v1/auth/token", json={"username": "admin", "password": "secret"}
        ).json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        listed = client.get("/api/v1/jobs", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["job_id"] == job_id
        assert client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["status"] == "queued"
        stages = client.get(f"/api/v1/jobs/{job_id}/stages", headers=headers)
        assert stages.status_code == 200
        assert stages.json() == {"workflow_id": None, "input_payload": None, "items": []}

        cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["status"] == "cancelled"
        assert client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers).status_code == 409


def test_cancelled_pending_effects_remain_visible_without_crm():
    """Cancellation is not rollback and the journal never exposes claim tokens."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    job_id, operation_id = str(uuid4()), str(uuid4())
    with Session(engine) as session:
        session.add(
            ResearchJob(
                job_id=job_id,
                kind="campaign",
                status="cancelled",
                claim_token="must-not-leak",
                result_outcome="partial",
            )
        )
        session.flush()
        session.add(
            CrmWriteOperation(
                operation_id=operation_id,
                job_id=job_id,
                stage_key="campaign.publication",
                object_name="malgCampaign",
                record_id=str(uuid4()),
                field_key="create",
                contract_version=1,
                contract_hash="hash",
                stable_operation_key="operation",
                intended_fields={"name": "Labelled campaign"},
                status="prepared",
            )
        )
        session.commit()
    with TestClient(
        create_app(database_engine=engine, auth_config=AuthConfig("admin", "secret"))
    ) as client:
        token = client.post(
            "/api/v1/auth/token", json={"username": "admin", "password": "secret"}
        ).json()["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        response = client.get(f"/api/v1/jobs/{job_id}/writes")
        assert response.status_code == 200
        assert response.json()["side_effects_pending"] is True
        assert response.json()["items"][0]["operation_id"] == operation_id
        assert "must-not-leak" not in response.text
        assert client.get(f"/api/v1/jobs/{job_id}").json()["result_outcome"] == "partial"
        assert client.get(f"/api/v1/jobs/{uuid4()}/writes").status_code == 404
