"""Authenticated HTTP-only job CLI contracts."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from malg.__main__ import main


@pytest.mark.parametrize(("status", "exit_code"), [(422, 2), (401, 1), (503, 1)])
def test_cli_distinguishes_rejection_from_auth_and_server_failure(
    monkeypatch, capsys, status, exit_code
):
    """Authentication and server failures must not masquerade as invalid input."""

    def request(method, url, **kwargs):
        return httpx.Response(status, json={"detail": "request rejected"})

    monkeypatch.setenv("MALG_API_URL", "https://api.example.test")
    monkeypatch.setenv("MALG_API_TOKEN", "secret-token")
    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setattr("sys.argv", ["malg", "jobs", "list"])

    assert main() == exit_code
    output = capsys.readouterr()
    assert "secret-token" not in output.out + output.err


def test_cli_invalid_file_and_missing_auth_are_invalid_input(monkeypatch, capsys, tmp_path: Path):
    """Missing local input and missing configuration fail before any HTTP request."""
    calls = 0

    def request(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("invalid local input must not call the API")

    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setenv("MALG_API_URL", "https://api.example.test")
    monkeypatch.setenv("MALG_API_TOKEN", "secret-token")
    monkeypatch.setattr(
        "sys.argv", ["malg", "jobs", "submit", "--request-file", str(tmp_path / "missing.json")]
    )
    assert main() == 2
    assert json.loads(capsys.readouterr().err) == {"error": "invalid_input"}

    monkeypatch.setattr("sys.argv", ["malg", "jobs", "list"])
    monkeypatch.delenv("MALG_API_TOKEN")
    assert main() == 2
    assert json.loads(capsys.readouterr().err) == {"error": "invalid_input"}
    assert calls == 0
