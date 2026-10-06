"""Observable authenticated Stats API range and coverage behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tests.fixtures import authenticated_client

from malg.database.models import Base, StatsState


def test_stats_api_requires_state_and_validates_range_parameters() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    client = authenticated_client(engine)
    authorization = client.headers.pop("Authorization")
    assert client.get("/api/v1/stats").status_code == 401
    client.headers["Authorization"] = authorization
    assert client.get("/api/v1/stats").status_code == 503
    with sessionmaker(engine).begin() as session:
        session.add(StatsState(id=1, collection_started_at=datetime.now(UTC)))
    assert client.get("/api/v1/stats", params={"hours": 0}).status_code == 422
    assert client.get("/api/v1/stats", params={"hours": 2161}).status_code == 422
    assert client.get("/api/v1/stats", params={"hours": "1.5"}).status_code == 422
    future = datetime.now(UTC) + timedelta(days=1)
    assert client.get("/api/v1/stats", params={"to": future.isoformat()}).status_code == 422
    assert client.get("/api/v1/stats", params={"worker_offset": -1}).status_code == 422
    assert client.get("/api/v1/stats/executions", params={"limit": 101}).status_code == 422
    assert client.get("/api/v1/stats", params={"to": "2026-01-01T00:00:00"}).status_code == 422
    response = client.get("/api/v1/stats", params={"hours": 1})
    assert response.status_code == 200
    payload = response.json()
    assert payload["window"]["bucket_seconds"] == 60
    assert [tool["source"] for tool in payload["tools"]] == ["search", "fetch", "browser_mcp"]
    assert payload["tools"][0]["totals"]["completed"] == 0
