"""Bounded, typed discovery through a private SearXNG instance."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from malg.config import SearchConfig
from malg.database.search_cache import SearchCache, default_search_cache

_MAX_QUERY_LENGTH = 300


class SearchRateLimitExceeded(RuntimeError):
    """Raised when a research run has exhausted its configured discovery budget."""


class SearchUnavailable(RuntimeError):
    """Raised when private search is disabled or SearXNG cannot return usable results."""


@dataclass(frozen=True)
class SearchResult:
    """One normalized, untrusted search result suitable for source selection."""

    title: str
    url: str
    snippet: str
    engines: tuple[str, ...]
    category: str | None


class SearxngSearchClient:
    """Search SearXNG with shared result reuse and per-agent request budgets.

    Queries are sent only to the configured private SearXNG endpoint. Returned titles and
    snippets are untrusted discovery hints, not evidence; callers must verify claims by visiting
    selected source URLs. Failed requests consume budget to avoid retry storms against upstream
    search engines.
    """

    def __init__(self, config: SearchConfig, *, cache: SearchCache | None = None) -> None:
        """Use the injected cache or the shared database configured by DATABASE_URL.

        Schema migration must precede searches. Construction creates no schema and sends no
        network request; the default cache lazily uses a shared connection pool.
        """
        self._config = config
        self._cache = cache if cache is not None else default_search_cache()
        self._request_count = 0
        self._last_request_at: float | None = None
        self._request_lock = asyncio.Lock()

    @property
    def request_count(self) -> int:
        """Return the number of discovery requests attempted in this agent run."""
        return self._request_count

    async def search(self, query: str, *, language: str = "en") -> list[SearchResult]:
        """Return bounded, normalized results for one research query.

        Args:
            query: A concise research query, without automatic external redirects.
            language: One configured result language.

        Raises:
            SearchUnavailable: If search is disabled, inputs are invalid, or SearXNG fails.
            SearchRateLimitExceeded: If this agent has consumed its search budget.
        """
        self._validate_query(query)
        if language not in self._config.languages:
            raise SearchUnavailable("Requested search language is not configured.")
        if not self._config.enabled:
            raise SearchUnavailable("Web search is disabled by configuration.")

        normalized_query = " ".join(query.split())
        request: dict[str, Any] = {
            "version": 1,
            "endpoint": self._config.url,
            "params": {
                "q": normalized_query,
                "format": "json",
                "language": language,
                "categories": ",".join(self._config.categories),
                "safesearch": 2,
            },
            "result_depth": self._config.max_results,
        }
        key = self._cache.key(request)
        counted_wait = False
        # Cover pacing, HTTP timeout, and publication without holding a DB transaction.
        lease_seconds = (
            self._config.timeout_seconds
            + self._config.max_requests_per_run * self._config.min_interval_seconds
            + 30
        )
        while True:
            lookup = await asyncio.to_thread(
                self._cache.lookup, request, lease_seconds=lease_seconds
            )
            if lookup.state == "hit":
                return self._normalize_results(lookup.payload)
            if lookup.state == "failed":
                raise SearchUnavailable("Private search request failed.")
            if lookup.state == "owner":
                assert lookup.token is not None
                break
            if not counted_wait:
                await asyncio.to_thread(self._cache.count, key, "coalesced_requests")
                counted_wait = True
            await asyncio.sleep(0.1)
        token = lookup.token
        try:
            await self._wait_for_request_slot()
            await asyncio.to_thread(self._cache.count, key, "upstream_requests")
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.timeout_seconds, connect=5.0)
            ) as client:
                response = await client.get(f"{self._config.url}/search", params=request["params"])
                response.raise_for_status()
                payload: object = response.json()
            results = self._normalize_results(payload)
            assert isinstance(payload, dict)
            if not results and payload.get("unresponsive_engines"):
                raise SearchUnavailable("Private search engines are unavailable.")
            # Partial engine outages are returned to this caller but never retained as healthy.
            if payload.get("unresponsive_engines"):
                await asyncio.to_thread(self._cache.release, key, token, failed=True)
            else:
                await asyncio.to_thread(
                    self._cache.finish, key, token, payload, self._config.cache_ttl_seconds
                )
            return results
        except BaseException as exc:
            await asyncio.shield(
                asyncio.to_thread(
                    self._cache.release,
                    key,
                    token,
                    failed=isinstance(exc, (httpx.HTTPError, SearchUnavailable, ValueError)),
                )
            )
            if isinstance(exc, (httpx.HTTPError, ValueError)):
                raise SearchUnavailable("Private search request failed.") from exc
            raise

    def _validate_query(self, query: str) -> None:
        if not isinstance(query, str) or not query.strip() or len(query) > _MAX_QUERY_LENGTH:
            raise SearchUnavailable(
                "Search query must be a non-empty string of at most 300 characters."
            )
        if "!!" in query:
            raise SearchUnavailable("Automatic external search redirects are not permitted.")

    async def _wait_for_request_slot(self) -> None:
        async with self._request_lock:
            if self._request_count >= self._config.max_requests_per_run:
                raise SearchRateLimitExceeded("Search request budget exhausted for this agent run.")
            now = time.monotonic()
            if self._last_request_at is not None:
                delay = self._config.min_interval_seconds - (now - self._last_request_at)
                if delay > 0:
                    await asyncio.sleep(delay)
            from malg.core.budget import reserve_external_attempt

            reserve_external_attempt("search")
            self._request_count += 1
            self._last_request_at = time.monotonic()

    def _normalize_results(self, payload: object) -> list[SearchResult]:
        if not isinstance(payload, Mapping):
            raise SearchUnavailable("Private search returned an invalid response.")
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise SearchUnavailable("Private search response did not contain results.")

        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for raw_result in raw_results:
            if len(results) >= self._config.max_results or not isinstance(raw_result, Mapping):
                continue
            result = self._normalize_result(raw_result)
            if result is None or result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            results.append(result)
        return results

    @staticmethod
    def _normalize_result(raw_result: Mapping[str, Any]) -> SearchResult | None:
        url = raw_result.get("url")
        if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
            return None
        title = raw_result.get("title")
        snippet = raw_result.get("content")
        engines = raw_result.get("engines")
        category = raw_result.get("category")
        return SearchResult(
            title=title.strip() if isinstance(title, str) else "",
            url=url,
            snippet=snippet.strip() if isinstance(snippet, str) else "",
            engines=tuple(engine for engine in engines if isinstance(engine, str))
            if isinstance(engines, list)
            else (),
            category=category if isinstance(category, str) else None,
        )
