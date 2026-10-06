"""Bounded, typed discovery through a private SearXNG instance."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from malg.config import SearchConfig
from malg.core.budget import BudgetExhausted, deny_external_attempt, reserve_external_attempt

_MAX_QUERY_LENGTH = 300


class SearchRateLimitExceeded(BudgetExhausted):
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
    """Search SearXNG with per-agent request budgets and serialized pacing.

    Queries are sent only to the configured private SearXNG endpoint. Returned titles and
    snippets are untrusted discovery hints, not evidence; callers must verify claims by visiting
    selected source URLs. Failed requests consume budget to avoid retry storms against upstream
    search engines.
    """

    def __init__(
        self, config: SearchConfig, *, request_slot: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        self._config = config
        self.request_slot = request_slot
        self.engine_errors: tuple[dict[str, str], ...] = ()
        self.health = "unknown"
        self._request_count = 0
        self._last_request_at: float | None = None
        self._request_lock = asyncio.Lock()

    @property
    def min_interval_seconds(self) -> float:
        """Return the configured spacing used by shared worker pacing."""
        return self._config.min_interval_seconds

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

        await self._wait_for_request_slot()
        self.engine_errors = ()
        self.health = "unavailable"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.timeout_seconds, connect=5.0)
            ) as client:
                response = await client.get(
                    f"{self._config.url}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "language": language,
                        "categories": ",".join(self._config.categories),
                        "safesearch": 2,
                    },
                )
                response.raise_for_status()
                payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchUnavailable("Private search request failed.") from exc
        results = self._normalize_results(payload)
        errors = payload.get("unresponsive_engines", []) if isinstance(payload, Mapping) else []
        self.engine_errors = (
            tuple(
                {"engine": str(item[0])[:80], "reason": str(item[1])[:200]}
                for item in errors[:20]
                if isinstance(item, (list, tuple)) and len(item) >= 2
            )
            if isinstance(errors, list)
            else ()
        )
        self.health = "degraded" if self.engine_errors else "healthy"
        if not results and self.engine_errors:
            self.health = "unavailable"
            raise SearchUnavailable("Search returned no usable hits while engines failed.")
        return results

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
                deny_external_attempt(
                    SearchRateLimitExceeded(
                        "Search request budget exhausted for this agent run.",
                        reason_code="search_agent_limit",
                        kind="search",
                        used=self._request_count,
                        limit=self._config.max_requests_per_run,
                    )
                )
            if self.request_slot is not None:
                await self.request_slot()
            now = time.monotonic()
            if self._last_request_at is not None:
                delay = self._config.min_interval_seconds - (now - self._last_request_at)
                if delay > 0:
                    await asyncio.sleep(delay)
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
        if (
            not isinstance(url, str)
            or len(url) > 2000
            or urlparse(url).scheme not in {"http", "https"}
        ):
            return None
        title = raw_result.get("title")
        snippet = raw_result.get("content")
        engines = raw_result.get("engines")
        category = raw_result.get("category")
        return SearchResult(
            title=title.strip()[:200] if isinstance(title, str) else "",
            url=url,
            snippet=snippet.strip()[:600] if isinstance(snippet, str) else "",
            engines=tuple(engine for engine in engines if isinstance(engine, str))
            if isinstance(engines, list)
            else (),
            category=category if isinstance(category, str) else None,
        )
