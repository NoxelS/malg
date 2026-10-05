"""Public retrieval cannot follow private redirects or exceed its request budget."""

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from email.message import Message
from io import BytesIO
from urllib.response import addinfourl

import pytest

from malg.core.budget import BudgetExhausted, ResearchBudget, bind_budget
from malg.core.retrieval import RetrievalService


def redirect_transport(
    monkeypatch: pytest.MonkeyPatch,
    destination: str | None,
    *,
    body: bytes = b"Observed public source",
    status: int = 200,
) -> list[str]:
    """Fake only DNS and the external HTTP connection, retaining urllib redirect handling."""
    calls = []

    def resolve(host, *args, **kwargs):
        address = "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))]

    def open_http(self, connection, request, **kwargs):
        calls.append(request.full_url)
        headers = Message()
        headers["Content-Type"] = "text/plain"
        if destination is not None:
            headers["Location"] = destination
        response = addinfourl(
            BytesIO(body),
            headers,
            request.full_url,
            302 if destination else status,
        )
        response.msg = "Found" if destination else "OK"
        return response

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr("urllib.request.AbstractHTTPHandler.do_open", open_http)
    return calls


def test_private_redirect_is_rejected_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = redirect_transport(monkeypatch, "http://127.0.0.1/private")
    result = asyncio.run(
        RetrievalService().fetch("http://public.example/source", purpose="evidence")
    )
    assert result.outcome == "unsafe_url"
    assert "access refused" in result.text
    assert not result.excerpts
    assert calls == ["http://public.example/source"]


def test_redirect_reserves_another_attempt_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = redirect_transport(monkeypatch, "http://second.example/source")
    budget = ResearchBudget(datetime.now(UTC) + timedelta(minutes=1), limits={"fetch": 1})
    reset = bind_budget(budget)
    try:
        with pytest.raises(BudgetExhausted):
            asyncio.run(
                RetrievalService().fetch("http://public.example/source", purpose="evidence")
            )
        assert budget.exhausted
        assert calls == ["http://public.example/source"]
    finally:
        reset()


def test_child_stages_cannot_reset_shared_fetch_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = redirect_transport(monkeypatch, None)
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    parent = ResearchBudget(deadline, limits={"fetch": 2})
    children = [parent.child(deadline), parent.child(deadline)]
    for index, child in enumerate(children):
        reset = bind_budget(child)
        try:
            result = asyncio.run(
                RetrievalService().fetch(f"http://public.example/{index}", purpose="evidence")
            )
            assert result.text == "Observed public source"
        finally:
            reset()
    reset = bind_budget(children[0])
    try:
        with pytest.raises(BudgetExhausted):
            asyncio.run(RetrievalService().fetch("http://public.example/third", purpose="evidence"))
        assert calls == ["http://public.example/0", "http://public.example/1"]
    finally:
        reset()


def test_linkedin_and_shortener_requests_never_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = redirect_transport(monkeypatch, None)
    for url in ("https://fr.linkedin.com/in/person", "https://lnkd.in/short"):
        result = asyncio.run(RetrievalService().fetch(url, purpose="evidence"))
        assert result.outcome == "unsafe_url"
        assert not result.excerpts
    assert calls == []


def test_encoded_linkedin_redirect_is_rejected_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = redirect_transport(monkeypatch, "https://www%2elinkedin.com/in/person")
    result = asyncio.run(
        RetrievalService().fetch("http://public.example/source", purpose="evidence")
    )
    assert result.outcome == "unsafe_url"
    assert calls == ["http://public.example/source"]


@pytest.mark.parametrize("status", [404, 410, 403, 429, 503])
def test_failed_pages_explain_the_failure_without_evidence(monkeypatch, status) -> None:
    """Missing pages are explicit while blocked and transient errors stay distinct."""
    calls = redirect_transport(monkeypatch, None, status=status)
    service = RetrievalService()
    result = asyncio.run(service.fetch("http://public.example/missing", purpose="evidence"))
    assert result.status_code == status
    assert result.text
    assert ("does not exist" in result.text) == (status in {404, 410})
    assert not result.excerpts
    assert asyncio.run(service.fetch(result.url, purpose="evidence")) == result
    assert service.observations == (result,)
    assert len(calls) == 1


@pytest.mark.parametrize("dns_error", [socket.EAI_NONAME, socket.EAI_AGAIN])
def test_dns_failure_explains_missing_or_temporarily_unavailable_host(
    monkeypatch, dns_error
) -> None:
    """A temporary DNS failure must not be presented as a nonexistent website."""

    def resolve(*args, **kwargs):
        raise socket.gaierror(dns_error, "DNS failure")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    result = asyncio.run(RetrievalService().fetch("http://missing.example/", purpose="evidence"))
    assert result.outcome == "unavailable"
    assert ("does not exist" in result.text) == (dns_error == socket.EAI_NONAME)
    assert not result.excerpts


def test_null_characters_are_normalized_before_agent_evidence(monkeypatch) -> None:
    """Agent-visible text and durable excerpts share PostgreSQL-safe exact quotes."""
    redirect_transport(monkeypatch, None, body=b"Observed\x00public source")
    result = asyncio.run(RetrievalService().fetch("http://public.example/", purpose="evidence"))
    assert result.text == "Observed public source"
    assert result.excerpts[0]["text"] == result.text
    assert "\x00" not in result.text


def test_invalid_idna_hostname_returns_an_observation_without_connecting(monkeypatch) -> None:
    calls = redirect_transport(monkeypatch, None)
    result = asyncio.run(
        RetrievalService().fetch(f"https://{'a' * 64}.example/", purpose="evidence")
    )
    assert result.outcome == "unsafe_url"
    assert not result.excerpts
    assert calls == []
