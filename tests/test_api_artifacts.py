"""Observable API outcomes for persisted campaign and ICP artifacts."""

from __future__ import annotations

import sqlite3
from copy import deepcopy
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from tests.test_campaign_research_agent import campaign_payload
from tests.test_icp_batch_runner import _icp

from malg.api.app import create_app
from malg.database import Base


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
    client = TestClient(create_app(database_engine=engine))
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
