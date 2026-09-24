"""Shared canonical fixtures for persistence API tests."""

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from malg.api.app import create_app
from malg.config import AuthConfig


def authenticated_client(engine: Engine) -> TestClient:
    """Create an API client with the deterministic test administrator token."""
    client = TestClient(
        create_app(database_engine=engine, auth_config=AuthConfig("admin", "test-password"))
    )
    token = client.post(
        "/api/v1/auth/token", json={"username": "admin", "password": "test-password"}
    ).json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
