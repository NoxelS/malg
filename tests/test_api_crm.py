"""Authenticated remote CRM reads remain independent of local history."""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from malg.api.app import create_app
from malg.config import AuthConfig, TwentyConfig
from malg.crm.client import TwentyClient


def _app_client(handler):
    """Inject the external HTTP boundary, leaving real adapter and routes active."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://twenty.test"
    )
    crm = TwentyClient(
        TwentyConfig(
            base_url="https://twenty.test",
            public_url="https://twenty.test",
            api_key="test",
            workspace_id="test",
            max_retries=0,
        ),
        transport,
    )
    return TestClient(
        create_app(
            database_engine=engine, auth_config=AuthConfig("admin", "secret"), crm_client=crm
        )
    )


def _authenticate(client):
    token = client.post(
        "/api/v1/auth/token", json={"username": "admin", "password": "secret"}
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"


def test_person_mononym_and_query_bound_cursor() -> None:
    """A native FullName renders text and a cursor cannot cross company filters."""
    person_id, company_id = uuid4(), uuid4()
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        node = {
            "id": str(person_id),
            "updatedAt": "2026-09-23T00:00:00Z",
            "name": {"firstName": "Cher", "lastName": ""},
            "jobTitle": None,
            "emails": {"primaryEmail": "", "additionalEmails": []},
            "linkedinLink": {"primaryLinkUrl": "", "secondaryLinks": []},
            "company": {"id": str(company_id)},
        }
        return httpx.Response(
            200,
            json={
                "data": {
                    "people": {
                        "edges": [{"node": node}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                    }
                }
            },
        )

    with _app_client(handler) as client:
        _authenticate(client)
        response = client.get("/api/v1/crm/people", params={"companyId": str(company_id)})
        assert response.status_code == 200, response.text
        assert response.json()["items"] == [
            {"id": str(person_id), "display_name": "Cher", "url": "https://twenty.test"}
        ]
        cursor = response.json()["next_cursor"]
        calls_before = len(calls)
        assert (
            client.get(
                "/api/v1/crm/people", params={"companyId": str(uuid4()), "cursor": cursor}
            ).status_code
            == 422
        )
        assert len(calls) == calls_before
        detail = client.get(f"/api/v1/crm/people/{person_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["display_name"] == "Cher"
        assert detail.json()["company_id"] == str(company_id)
        assert (
            client.get(
                "/api/v1/crm/people", params={"companyId": str(company_id), "cursor": cursor}
            ).status_code
            == 502
        )


def test_outage_blocks_remote_reads_not_local_health() -> None:
    """Unavailable CRM is truthful while local liveness and readiness remain usable."""

    def handler(request):
        return httpx.Response(503)

    with _app_client(handler) as client:
        _authenticate(client)
        status = client.get("/api/v1/crm/status")
        assert status.status_code == 200
        assert status.json()["available"] is False
        assert status.json()["schema_compatible"] is False
        assert status.json()["reason"] == "crm_unavailable"
        assert client.get("/api/v1/crm/campaigns").status_code == 503
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200


def test_missing_detail_and_malformed_projection_are_distinct() -> None:
    """Absence is 404, while an invalid upstream projection is a 502 failure."""
    responses = [
        {"edges": []},
        {"edges": [{"node": {"id": "broken"}}]},
    ]

    def handler(request):
        return httpx.Response(200, json={"data": {"malgCampaigns": responses.pop(0)}})

    with _app_client(handler) as client:
        _authenticate(client)
        assert client.get(f"/api/v1/crm/campaigns/{uuid4()}").status_code == 404
        assert client.get(f"/api/v1/crm/campaigns/{uuid4()}").status_code == 502
