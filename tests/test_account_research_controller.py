"""Controller persistence and operator-facing state contracts."""

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from tests.fixtures import authenticated_client

from malg.database.models import Base


def test_controller_starts_disabled_and_requires_configuration_to_enable() -> None:
    """A fresh installation cannot begin continuous research without saved scope."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    client = authenticated_client(engine)

    initial = client.get("/api/v1/account-research-controller")
    assert initial.status_code == 200
    assert initial.json()["enabled"] is False
    assert initial.json()["configuration"] is None
    assert initial.json()["queued_matching_jobs"] == 0

    rejected = client.patch(
        "/api/v1/account-research-controller/state", json={"enabled": True, "revision": 0}
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "controller_configuration_required"
