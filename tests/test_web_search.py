"""Observable tests for bounded private SearXNG discovery."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from sqlalchemy import create_engine, func, select, update

from malg.config import SearchConfig
from malg.core import web_search
from malg.core.web_search import SearchRateLimitExceeded, SearchUnavailable, SearxngSearchClient
from malg.database.models import SearchCacheEntry
from malg.database.search_cache import SearchCache
from malg.database.session import make_session_factory


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


@pytest.fixture
def sessions(tmp_path: Path):
    """Provide a real shared database with independent connection-scoped transactions."""
    engine = create_engine(f"sqlite:///{tmp_path / 'cache.db'}")
    SearchCacheEntry.__table__.create(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def cache(sessions):
    return SearchCache(sessions)


def _metrics(sessions) -> tuple[int, int, int]:
    # Inspect persisted counters through the public schema.
    with sessions() as session:
        return tuple(
            session.execute(
                select(
                    func.sum(SearchCacheEntry.upstream_requests),
                    func.sum(SearchCacheEntry.cache_hits),
                    func.sum(SearchCacheEntry.coalesced_requests),
                )
            ).one()
        )


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


def test_search_normalizes_results_and_bounds_request_parameters(monkeypatch, cache) -> None:
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

    results = asyncio.run(
        SearxngSearchClient(_config(), cache=cache).search("AI services", language="de")
    )

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


def test_search_rejects_redirect_syntax_and_unconfigured_language(cache) -> None:
    client = SearxngSearchClient(_config(), cache=cache)

    with pytest.raises(SearchUnavailable, match="redirects"):
        asyncio.run(client.search("!! redirect"))
    with pytest.raises(SearchUnavailable, match="language"):
        asyncio.run(client.search("AI services", language="fr"))


def test_search_budget_counts_successful_attempts(monkeypatch, cache) -> None:
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": []}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client = SearxngSearchClient(_config(max_requests_per_run=1), cache=cache)

    assert asyncio.run(client.search("first")) == []
    with pytest.raises(SearchRateLimitExceeded, match="budget"):
        asyncio.run(client.search("second"))
    assert client.request_count == 1


def test_search_cache_is_shared_and_keeps_query_dimensions_distinct(
    monkeypatch, cache, sessions
) -> None:
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": [{"url": "https://example.test/a"}]}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    first = SearxngSearchClient(_config(), cache=cache)
    second = SearxngSearchClient(_config(), cache=cache)

    asyncio.run(first.search("  AI   services ", language="en"))
    asyncio.run(second.search("AI services", language="en"))
    asyncio.run(second.search("AI services", language="de"))

    assert len(_FakeHTTPClient.calls) == 2
    assert first.request_count == 1
    assert second.request_count == 1
    assert _metrics(sessions) == (2, 1, 0)


def test_concurrent_equivalent_searches_are_coalesced(monkeypatch, cache, sessions) -> None:
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": [{"url": "https://example.test/a"}]}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client_a = SearxngSearchClient(_config(), cache=cache)
    client_b = SearxngSearchClient(_config(), cache=cache)

    async def run_both() -> tuple[list, list]:
        return await asyncio.gather(client_a.search("same query"), client_b.search("same query"))

    first, second = asyncio.run(run_both())
    assert first == second
    assert len(_FakeHTTPClient.calls) == 1
    assert _metrics(sessions) == (1, 1, 1)


def test_cached_response_survives_connection_pool_and_run_restart(monkeypatch, tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'persistent.db'}"
    engine = create_engine(url)
    SearchCacheEntry.__table__.create(engine)
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": [{"url": "https://example.test/a"}], "extra": "retained"}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    first = SearxngSearchClient(_config(), cache=SearchCache(make_session_factory(engine)))
    expected = asyncio.run(first.search("persistent"))
    engine.dispose()
    engine = create_engine(url)
    store = SearchCache(make_session_factory(engine))
    second = SearxngSearchClient(_config(), cache=store)
    assert asyncio.run(second.search("persistent")) == expected
    assert second.request_count == 0
    assert len(_FakeHTTPClient.calls) == 1
    with make_session_factory(engine)() as session:
        assert session.scalar(select(SearchCacheEntry.response)) == _FakeHTTPClient.payload
    engine.dispose()


def test_expired_response_refreshes_and_outage_is_not_hidden(monkeypatch, cache, sessions) -> None:
    from datetime import UTC, datetime, timedelta

    import httpx

    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": [{"url": "https://example.test/old"}]}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client = SearxngSearchClient(_config(max_requests_per_run=10), cache=cache)
    asyncio.run(client.search("refresh"))
    with sessions.begin() as session:
        session.execute(
            update(SearchCacheEntry).values(expires_at=datetime.now(UTC) - timedelta(seconds=10))
        )
    _FakeHTTPClient.payload = {"results": [{"url": "https://example.test/new"}]}
    assert asyncio.run(client.search("refresh"))[0].url == "https://example.test/new"
    with sessions.begin() as session:
        session.execute(
            update(SearchCacheEntry).values(expires_at=datetime.now(UTC) - timedelta(seconds=10))
        )

    class FailingClient(_FakeHTTPClient):
        async def get(self, url, *, params):
            await asyncio.sleep(0.05)
            raise httpx.ConnectError("provider unavailable")

    monkeypatch.setattr(web_search.httpx, "AsyncClient", FailingClient)

    async def fail_together():
        return await asyncio.gather(
            client.search("refresh"), client.search("refresh"), return_exceptions=True
        )

    failures = asyncio.run(fail_together())
    assert all(isinstance(exc, SearchUnavailable) for exc in failures)


def test_search_identity_preserves_endpoint_categories_and_depth(monkeypatch, cache) -> None:
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": []}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    for config in (
        _config(),
        _config(url="http://other:8080"),
        _config(categories=("news",)),
        _config(max_results=3),
    ):
        asyncio.run(SearxngSearchClient(config, cache=cache).search("identity"))
    assert len(_FakeHTTPClient.calls) == 4


def test_partial_engine_outage_is_not_cached(monkeypatch, cache, sessions) -> None:
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": [], "unresponsive_engines": [["engine", "timeout"]]}
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeHTTPClient)
    client = SearxngSearchClient(_config(), cache=cache)
    with pytest.raises(SearchUnavailable):
        asyncio.run(client.search("partial"))
    with sessions() as session:
        assert session.scalar(select(SearchCacheEntry.response)) is None
    with pytest.raises(SearchUnavailable):
        asyncio.run(client.search("partial"))
