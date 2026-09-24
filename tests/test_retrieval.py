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


def redirect_transport(monkeypatch: pytest.MonkeyPatch, destination: str | None) -> list[str]:
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
            BytesIO(b"Observed public source"),
            headers,
            request.full_url,
            302 if destination else 200,
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
    assert result.text == ""
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
