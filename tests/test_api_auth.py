"""Observable authentication outcomes for the versioned API."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.test_api_artifacts import _engine

from malg.api.app import create_app
from malg.config import AuthConfig


def test_login_and_bearer_protection() -> None:
    """Configured credentials issue tokens and protect data routes."""
    client = TestClient(
        create_app(database_engine=_engine(), auth_config=AuthConfig("admin", "secret"))
    )
    assert client.get("/api/v1/dashboard").status_code == 401
    assert client.get("/api/v1/dashboard").headers["www-authenticate"] == "Bearer"
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert (
        client.post(
            "/api/v1/auth/token", json={"username": "admin", "password": "wrong"}
        ).status_code
        == 401
    )
    response = client.post("/api/v1/auth/token", json={"username": "admin", "password": "secret"})
    assert response.json()["token_type"] == "bearer"
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/dashboard", headers=headers).status_code == 200
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert (
        client.get("/api/v1/dashboard", headers={"Authorization": f"Bearer {tampered}"}).status_code
        == 401
    )


def test_blank_password_fails_closed() -> None:
    """Unconfigured authentication rejects login and all versioned routes."""
    client = TestClient(create_app(database_engine=_engine(), auth_config=AuthConfig("admin", "")))
    response = client.post("/api/v1/auth/token", json={"username": "admin", "password": "secret"})
    assert response.status_code == 503
    assert response.json() == {"detail": "authentication is not configured"}
    assert client.get("/api/v1/dashboard").status_code == 401
