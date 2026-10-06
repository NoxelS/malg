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
from malg.database.search_cache import SearchCache, default_search_cache

_MAX_QUERY_LENGTH = 300
SEARCH_OUTAGE_REASON_CODES = frozenset(
    {
        "search_unavailable",
        "search_captcha",
        "search_rate_limited",
        "search_provider_blocked",
        "search_provider_failure",
        "search_http_error",
        "search_transport_error",
        "search_parser_failure",
    }
)


class SearchRateLimitExceeded(BudgetExhausted):
    """Raised when a research run has exhausted its configured discovery budget."""


class SearchUnavailable(RuntimeError):
    """Raised when private search is disabled or SearXNG cannot return usable results."""

    def __init__(self, message: str, *, reason_code: str = "search_unavailable") -> None:
        """Carry a safe provider failure category for persisted outage diagnostics."""
        super().__init__(message)
        self.reason_code = reason_code


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
        self,
        config: SearchConfig,
        *,
        request_slot: Callable[[], Awaitable[None]] | None = None,
        cache: SearchCache | None = None,
    ) -> None:
        """Use an injected cache or the shared DATABASE_URL pool and optional worker pacing.

        Apply the cache migration before searching. Cache hits bypass external request pacing
        and budgets; misses reserve an attempt before sending HTTP. Construction creates no schema.
        """
        self._config = config
        self._cache = cache if cache is not None else default_search_cache()
        self.request_slot = request_slot
        self.engine_errors: tuple[dict[str, str], ...] = ()
        self.health = "unknown"
        self.failure_reason_code: str | None = None
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

        self.engine_errors = ()
        self.health = "unavailable"
        self.failure_reason_code = None
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
                return self._observe(lookup.payload)
            if lookup.state == "failed":
                self.failure_reason_code = "search_unavailable"
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
            results = self._observe(payload)
            # Partial engine failures are returned but never retained as healthy.
            if self.engine_errors:
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
                    failed=isinstance(exc, (httpx.HTTPError, ValueError, SearchUnavailable)),
                )
            )
            if isinstance(exc, SearchUnavailable):
                self.failure_reason_code = exc.reason_code
                raise
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                reason_code = (
                    "search_rate_limited"
                    if status == 429
                    else "search_provider_blocked"
                    if status in {401, 403}
                    else "search_http_error"
                )
                message = "Private search returned an HTTP error."
            elif isinstance(exc, httpx.HTTPError):
                reason_code = "search_transport_error"
                message = "Private search request failed."
            elif isinstance(exc, ValueError):
                reason_code = "search_parser_failure"
                message = "Private search returned invalid JSON."
            else:
                raise
            self.failure_reason_code = reason_code
            raise SearchUnavailable(message, reason_code=reason_code) from exc

    def _observe(self, payload: object) -> list[SearchResult]:
        """Restore engine diagnostics identically for fresh and cached search responses."""
        try:
            results = self._normalize_results(payload)
        except SearchUnavailable as exc:
            self.failure_reason_code = exc.reason_code
            raise
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
            reason_code = _engine_failure_reason(self.engine_errors)
            self.failure_reason_code = reason_code
            raise SearchUnavailable(
                "Search returned no usable hits while engines failed.", reason_code=reason_code
            )
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
            raise SearchUnavailable(
                "Private search returned an invalid response.", reason_code="search_parser_failure"
            )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise SearchUnavailable(
                "Private search response did not contain results.",
                reason_code="search_parser_failure",
            )

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
        if raw_results and not results:
            raise SearchUnavailable(
                "Private search returned results without any usable result entries.",
                reason_code="search_parser_failure",
            )
        return results

    @staticmethod
    def _normalize_result(raw_result: Mapping[str, Any]) -> SearchResult | None:
        url = raw_result.get("url")
        if not isinstance(url, str) or len(url) > 2000:
            return None
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return None
        except ValueError:
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


def _engine_failure_reason(errors: tuple[dict[str, str], ...]) -> str:
    """Classify provider diagnostics without exposing their raw text as a public error."""
    diagnostics = " ".join(item.get("reason", "") for item in errors).casefold()
    if "captcha" in diagnostics or "bot" in diagnostics:
        return "search_captcha"
    if "rate" in diagnostics or "429" in diagnostics or "too many" in diagnostics:
        return "search_rate_limited"
    return "search_provider_failure"
