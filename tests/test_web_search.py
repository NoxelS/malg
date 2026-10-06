"""Observable tests for bounded private SearXNG discovery."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, ClassVar, cast

import pytest

from malg.config import SearchConfig
from malg.core import web_search
from malg.core.web_search import SearchRateLimitExceeded, SearchUnavailable, SearxngSearchClient


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeHTTPClient:
    calls: ClassVar[list[dict[str, Any]]] = []
    payload: ClassVar[object] = {}

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> _FakeHTTPClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, *, params: dict[str, object]) -> _FakeResponse:
        self.calls.append({"url": url, "params": params})
        return _FakeResponse(self.payload)


def _config(**overrides: object) -> SearchConfig:
    values: dict[str, object] = {
        "enabled": True,
        "url": "http://searxng:8080",
        "timeout_seconds": 20,
        "max_results": 2,
        "max_requests_per_run": 2,
        "min_interval_seconds": 0,
        "languages": ("en", "de"),
        "categories": ("general",),
    }
    values.update(overrides)
    return SearchConfig(**cast(dict[str, Any], values))


def test_search_normalizes_results_and_bounds_request_parameters(monkeypatch) -> None:
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {
        "results": [
            {
                "title": " First result ",
                "url": "https://example.test/first",
                "content": " A discovery hint ",
                "engines": ["duckduckgo", 4],
                "category": "general",
            },
            {"title": "Duplicate", "url": "https://example.test/first"},
            {"title": "Unsupported", "url": "file:///private"},
            {"title": "Second", "url": "https://example.test/second"},
            {"title": "Over limit", "url": "https://example.test/third"},
        ]
    }
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)

    results = asyncio.run(SearxngSearchClient(_config()).search("AI services", language="de"))

    assert [(result.title, result.url, result.snippet, result.engines) for result in results] == [
        ("First result", "https://example.test/first", "A discovery hint", ("duckduckgo",)),
        ("Second", "https://example.test/second", "", ()),
    ]
    assert _FakeHTTPClient.calls == [
        {
            "url": "http://searxng:8080/search",
            "params": {
                "q": "AI services",
                "format": "json",
                "language": "de",
                "categories": "general",
                "safesearch": 2,
            },
        }
    ]


def test_search_rejects_redirect_syntax_and_unconfigured_language() -> None:
    client = SearxngSearchClient(_config())

    with pytest.raises(SearchUnavailable, match="redirects"):
        asyncio.run(client.search("!! redirect"))
    with pytest.raises(SearchUnavailable, match="language"):
        asyncio.run(client.search("AI services", language="fr"))


def test_search_budget_counts_successful_attempts(monkeypatch) -> None:
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": []}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client = SearxngSearchClient(_config(max_requests_per_run=1))

    assert asyncio.run(client.search("first")) == []
    with pytest.raises(SearchRateLimitExceeded, match="budget"):
        asyncio.run(client.search("second"))
    assert client.request_count == 1


@pytest.mark.parametrize(
    ("diagnostic", "reason_code"),
    [("CAPTCHA required", "search_captcha"), ("429 rate limit", "search_rate_limited")],
)
def test_provider_failures_are_classified_without_becoming_empty_success(
    monkeypatch, diagnostic, reason_code
) -> None:
    _FakeHTTPClient.payload = {"results": [], "unresponsive_engines": [["engine", diagnostic]]}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client = SearxngSearchClient(_config())

    with pytest.raises(SearchUnavailable) as error:
        asyncio.run(client.search("AI services"))

    assert client.health == "unavailable"
    assert client.failure_reason_code == reason_code
    assert error.value.reason_code == reason_code


def test_healthy_empty_search_remains_distinct_from_provider_failure(monkeypatch) -> None:
    _FakeHTTPClient.payload = {"results": [], "unresponsive_engines": []}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client = SearxngSearchClient(_config())

    assert asyncio.run(client.search("nothing found")) == []
    assert client.health == "healthy"
    assert client.failure_reason_code is None


def test_search_agent_denial_remains_visible_after_generated_code_catches_it(monkeypatch) -> None:
    """The smaller search-client allowance must not become a model-reported false budget."""
    from datetime import UTC, datetime, timedelta

    from malg.core.budget import ResearchBudget, bind_budget

    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": []}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client = SearxngSearchClient(_config(max_requests_per_run=1))
    budget = ResearchBudget(datetime.now(UTC) + timedelta(minutes=5), limits={"search": 10})

    async def run():
        reset = bind_budget(budget)
        try:
            await client.search("first")
            with suppress(SearchRateLimitExceeded):
                await client.search("second")
        finally:
            reset()

    asyncio.run(run())
    assert budget.exhausted
    assert budget.exhaustion.diagnostics() == {
        "reason_code": "search_agent_limit",
        "kind": "search",
        "used": 1,
        "limit": 1,
    }
    assert client.request_count == 1
    assert len(_FakeHTTPClient.calls) == 1
