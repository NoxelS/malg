"""Observable tests for bounded private SearXNG discovery."""

from __future__ import annotations

import asyncio
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
        await asyncio.sleep(0.01)
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
    web_search.clear_search_cache()
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
    web_search.clear_search_cache()
    client = SearxngSearchClient(_config())

    with pytest.raises(SearchUnavailable, match="redirects"):
        asyncio.run(client.search("!! redirect"))
    with pytest.raises(SearchUnavailable, match="language"):
        asyncio.run(client.search("AI services", language="fr"))


def test_search_budget_counts_successful_attempts(monkeypatch) -> None:
    web_search.clear_search_cache()
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": []}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client = SearxngSearchClient(_config(max_requests_per_run=1))

    assert asyncio.run(client.search("first")) == []
    with pytest.raises(SearchRateLimitExceeded, match="budget"):
        asyncio.run(client.search("second"))
    assert client.request_count == 1


def test_search_cache_is_shared_and_keeps_query_dimensions_distinct(monkeypatch) -> None:
    web_search.clear_search_cache()
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": [{"url": "https://example.test/a"}]}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    first = SearxngSearchClient(_config())
    second = SearxngSearchClient(_config())

    asyncio.run(first.search("  AI   services ", language="en"))
    asyncio.run(second.search("ai services", language="en"))
    asyncio.run(second.search("ai services", language="de"))

    assert len(_FakeHTTPClient.calls) == 2
    assert first.request_count == 1
    assert second.request_count == 1
    assert web_search.get_search_metrics().upstream_requests == 2
    assert web_search.get_search_metrics().cache_hits == 1


def test_concurrent_equivalent_searches_are_coalesced(monkeypatch) -> None:
    web_search.clear_search_cache()
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": [{"url": "https://example.test/a"}]}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client_a = SearxngSearchClient(_config())
    client_b = SearxngSearchClient(_config())

    async def run_both() -> tuple[list, list]:
        return await asyncio.gather(client_a.search("same query"), client_b.search("same query"))

    first, second = asyncio.run(run_both())
    assert first == second
    assert len(_FakeHTTPClient.calls) == 1
    assert web_search.get_search_metrics().coalesced_requests == 1
