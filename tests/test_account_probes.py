"""Offline observable checks for account probe safety boundaries."""

from __future__ import annotations

import asyncio
import socket

from pydantic import HttpUrl, TypeAdapter

from malg.core import account_probes
from malg.core.account_probes import AccountProbeService
from malg.core.models.account import CheckOutcome


def _url(value: str) -> HttpUrl:
    """Build a validated URL fixture without performing network I/O."""
    return TypeAdapter(HttpUrl).validate_python(value)


def test_url_probe_rejects_non_public_destination_before_http(monkeypatch) -> None:
    monkeypatch.setattr(account_probes, "_is_public_host", lambda hostname: False)

    result = asyncio.run(AccountProbeService().probe_url(_url("https://example.test/")))

    assert result.outcome is CheckOutcome.FAIL
    assert result.status_code is None
    assert result.reason == "URL resolves to a non-public or unavailable destination."


def test_mail_domain_probe_never_claims_a_mailbox_exists(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )

    result = asyncio.run(AccountProbeService().probe_mail_domain("example.test"))

    assert result.outcome is CheckOutcome.INCONCLUSIVE
    assert result.accepts_mail is None
    assert result.reason == "Domain resolves; MX or null-MX was not checked."
