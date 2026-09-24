"""Offline observable checks for account probe safety boundaries."""

from __future__ import annotations

import asyncio
import socket

import httpx
from pydantic import HttpUrl, TypeAdapter

from malg.core.account_probes import AccountProbeService
from malg.core.models.account import (
    AccountData,
    AccountIdentity,
    AccountResearchResult,
    AccountValidationOutcome,
    CheckOutcome,
)


def _url(value: str) -> HttpUrl:
    """Build a validated URL fixture without performing network I/O."""
    return TypeAdapter(HttpUrl).validate_python(value)


def test_url_probe_rejects_non_public_destination_before_http(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )

    result = asyncio.run(AccountProbeService().probe_url(_url("https://example.test/")))

    assert result.outcome is CheckOutcome.FAIL
    assert result.status_code is None


def test_mail_domain_probe_never_claims_a_mailbox_exists(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )

    result = asyncio.run(AccountProbeService().probe_mail_domain("example.test"))

    assert result.outcome is CheckOutcome.INCONCLUSIVE
    assert result.accepts_mail is None


def test_company_probes_do_not_visit_linkedin_identifiers(monkeypatch) -> None:
    visited: list[str] = []
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )

    async def request(self, request):
        visited.append(str(request.url))
        return httpx.Response(200, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", request)
    result = asyncio.run(
        AccountProbeService().probe_candidate(
            AccountResearchResult(
                outcome="complete",
                qualification=AccountValidationOutcome.ACCEPTED,
                identity=AccountIdentity(
                    display_name="Public fixture", official_website=_url("https://example.test/")
                ),
                data=AccountData(
                    name="Public fixture",
                    website=_url("https://example.test/"),
                    linkedin_url=_url("https://www.linkedin.com/company/example/"),
                ),
            )
        )
    )
    assert visited == ["https://example.test/"]
    assert len(result.url_checks) == 1
    assert result.url_checks[0].outcome is CheckOutcome.PASS

    blocked = asyncio.run(
        AccountProbeService().probe_url(_url("https://www.linkedin.com/company/example/"))
    )
    assert blocked.outcome is CheckOutcome.FAIL
    assert visited == ["https://example.test/"]
