"""Bounded, typed discovery through a private SearXNG instance."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx

from malg.config import SearchConfig
from malg.core.budget import BudgetExhausted, deny_external_attempt, reserve_external_attempt
from malg.core.models.stats import ToolOutcome, ToolRequestIssueRecord
from malg.core.stats import begin_tool_request, finish_tool_request
from malg.database.search_cache import SearchCache, default_search_cache
from malg.search_codes import SEARCH_OUTAGE_REASON_CODES as SEARCH_OUTAGE_REASON_CODES

_MAX_QUERY_LENGTH = 300

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_URL_RE = re.compile(r"(?:https?://|ftp://|www\.)\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_CREDENTIAL_RE = re.compile(
    r"\b(?:authorization|bearer|token|api[_ -]?key|password|secret|credential)"
    r"\s*(?:[:=]\s*|\s+)[\"']?[^\s,;\"']+",
    re.IGNORECASE,
)
_OPAQUE_TOKEN_RE = re.compile(
    r"\b(?=[A-Za-z0-9_-]{24,}\b)(?=[A-Za-z0-9_-]*[A-Za-z])"
    r"(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b"
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

        request_id, started_at, started_ns = begin_tool_request()
        cache_hit = False
        coalesced = False
        outbound_attempted = False
        http_status: int | None = None
        result_count: int | None = 0
        issues: tuple[ToolRequestIssueRecord, ...] = ()
        logical_issues: tuple[ToolRequestIssueRecord, ...] = ()
        terminal_override: tuple[ToolOutcome, str | None] | None = (
            "rejected",
            "invalid_input",
        )
        recorded = False

        def record(outcome: ToolOutcome, reason_code: str | None = None) -> None:
            nonlocal recorded
            if recorded:
                return
            finish_tool_request(
                request_id=request_id,
                started_at=started_at,
                started_ns=started_ns,
                source="search",
                operation="search",
                outcome=outcome,
                cache_hit=cache_hit,
                coalesced=coalesced,
                outbound_attempted=outbound_attempted,
                http_status=http_status,
                reason_code=reason_code,
                result_count=result_count,
                issues=issues if outbound_attempted else (),
            )
            recorded = True

        try:
            self._validate_query(query)
            terminal_override = None
            if language not in self._config.languages:
                terminal_override = ("rejected", "unsupported_language")
                raise SearchUnavailable("Requested search language is not configured.")
            if not self._config.enabled:
                terminal_override = ("rejected", "search_disabled")
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
                    cache_hit = True
                    result_count = 0
                    logical_issues = _engine_issues(lookup.payload)
                    results = self._observe(lookup.payload)
                    result_count = len(results)
                    record("degraded" if logical_issues else "success")
                    return results
                if lookup.state == "failed":
                    self.failure_reason_code = "search_unavailable"
                    result_count = 0
                    terminal_override = ("error", "shared_failure")
                    raise SearchUnavailable("Private search request failed.")
                if lookup.state == "owner":
                    assert lookup.token is not None
                    break
                if not counted_wait:
                    coalesced = True
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
                    outbound_attempted = True
                    response = await client.get(
                        f"{self._config.url}/search", params=request["params"]
                    )
                    response_status = getattr(response, "status_code", None)
                    http_status = response_status if isinstance(response_status, int) else None
                    response.raise_for_status()
                    payload: object = response.json()
                logical_issues = _engine_issues(payload)
                issues = logical_issues
                results = self._observe(payload)
                result_count = len(results)
                if logical_issues:
                    await asyncio.to_thread(self._cache.release, key, token, failed=True)
                else:
                    await asyncio.to_thread(
                        self._cache.finish,
                        key,
                        token,
                        cast(dict[str, Any], payload),
                        self._config.cache_ttl_seconds,
                    )
                record("degraded" if logical_issues else "success")
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
                    http_status = status
                    reason_code = (
                        "search_rate_limited"
                        if status == 429
                        else "search_provider_blocked"
                        if status in {401, 403}
                        else "search_http_error"
                    )
                    terminal_override = (
                        "blocked" if status in {403, 429} else "error",
                        reason_code,
                    )
                    message = "Private search returned an HTTP error."
                elif isinstance(exc, httpx.HTTPError):
                    reason_code = "search_transport_error"
                    terminal_override = ("error", reason_code)
                    message = "Private search request failed."
                elif isinstance(exc, ValueError):
                    reason_code = "search_parser_failure"
                    terminal_override = ("error", reason_code)
                    message = "Private search returned invalid JSON."
                else:
                    raise
                self.failure_reason_code = reason_code
                raise SearchUnavailable(message, reason_code=reason_code) from exc
        except asyncio.CancelledError:
            record("cancelled", "cancelled")
            raise
        except BudgetExhausted:
            record("rejected", "budget_exhausted")
            raise
        except SearchUnavailable as exc:
            if terminal_override is not None:
                outcome, override_reason_code = terminal_override
                reason_code = override_reason_code or exc.reason_code
            elif logical_issues:
                outcome = (
                    "degraded"
                    if result_count
                    else "blocked"
                    if any(issue.category == "block" for issue in logical_issues)
                    else "error"
                )
                reason_code = exc.reason_code
            else:
                outcome, reason_code = "error", exc.reason_code
            record(outcome, reason_code)
            raise
        except Exception:
            record("error", "tool_error")
            raise

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


def _engine_issues(payload: object) -> tuple[ToolRequestIssueRecord, ...]:
    if not isinstance(payload, Mapping):
        return ()
    diagnostics = payload.get("unresponsive_engines")
    if not isinstance(diagnostics, list):
        return ()

    occurred_at = datetime.now(UTC)
    normalized: list[ToolRequestIssueRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for diagnostic in diagnostics[:20]:
        if not isinstance(diagnostic, (list, tuple)) or len(diagnostic) < 2:
            continue
        engine = _safe_engine_label(diagnostic[0])
        message, category = _safe_diagnostic_message(diagnostic[1])
        key = (engine or "", category, message)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            ToolRequestIssueRecord(
                occurred_at=occurred_at,
                source="search",
                engine=engine,
                category=category,
                message=message,
            )
        )
    return tuple(normalized)


def _safe_engine_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(_CONTROL_RE.sub(" ", value).split())
    if (
        not cleaned
        or _URL_RE.search(cleaned)
        or _EMAIL_RE.search(cleaned)
        or _CREDENTIAL_RE.search(cleaned)
        or _OPAQUE_TOKEN_RE.search(cleaned)
        or re.search(r"(?i)\b(?:token|secret|password|credential|authorization)\b", cleaned)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]*", cleaned)
    ):
        return None
    return cleaned[:80]


def _safe_diagnostic_message(value: object) -> tuple[str, Literal["block", "error"]]:
    raw = value if isinstance(value, str) else ""
    cleaned = _CONTROL_RE.sub(" ", raw)
    cleaned = _URL_RE.sub("[redacted]", cleaned)
    cleaned = _EMAIL_RE.sub("[redacted]", cleaned)
    cleaned = _CREDENTIAL_RE.sub("[redacted]", cleaned)
    cleaned = _OPAQUE_TOKEN_RE.sub("[redacted]", cleaned)
    text = " ".join(cleaned.split())
    folded = text.casefold()

    if "searxenginecaptchaexception" in folded:
        message = "SearxEngineCaptchaException"
        if "captcha required" in folded:
            message += ": CAPTCHA required"
        return message[:200], "block"
    if re.search(r"\bcaptcha\b", folded):
        message = (
            "CAPTCHA required" if re.search(r"\b(?:required|challenge)\b", folded) else "CAPTCHA"
        )
        return message, "block"

    status_match = re.search(
        r"\bHTTP(?:(?:\s+(?:status|error))|(?:statuserror|error))?"
        r"\s*[:=]?\s*([1-5]\d{2})\b",
        folded,
    )
    if status_match:
        status = int(status_match.group(1))
        block = status in {403, 429} or bool(
            re.search(
                r"\b(?:forbidden|access denied|too many requests|rate[ -]?limit|blocked)\b",
                folded,
            )
        )
        suffix = {
            "forbidden": " Forbidden",
            "access denied": " Access denied",
            "too many requests": " Too many requests",
            "rate limit": " Rate limit",
            "service unavailable": " Service unavailable",
            "blocked": " Blocked",
        }
        detail = next((value for key, value in suffix.items() if key in folded), "")
        if not detail and re.search(r"\brate[ -]?limit\b", folded):
            detail = " Rate limit"
        return f"HTTP {status}{detail}"[:200], "block" if block else "error"
    block_status = re.search(r"\b(403|429)\b", folded)
    if re.search(r"\b(?:too many requests|rate[ -]?limit(?:ed|ing)?)\b", folded):
        message = "Too many requests" if "too many" in folded else "Rate limit"
        if block_status:
            message = f"HTTP {block_status.group(1)} {message}"
        return message, "block"
    if re.search(r"\baccess denied\b", folded):
        return (
            f"HTTP {block_status.group(1)} Access denied" if block_status else "Access denied",
            "block",
        )
    if re.search(r"\bforbidden\b", folded):
        return (
            f"HTTP {block_status.group(1)} Forbidden" if block_status else "Forbidden",
            "block",
        )
    if re.search(r"\bblocked\b", folded):
        return (
            f"HTTP {block_status.group(1)} Blocked" if block_status else "Blocked",
            "block",
        )
    known_exceptions = (
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutError",
        "ConnectionError",
        "ConnectError",
        "ReadError",
        "HTTPStatusError",
        "HTTPError",
        "SSLError",
        "ProxyError",
        "NameResolutionError",
        "RemoteProtocolError",
    )
    exception_name = next(
        (name for name in known_exceptions if re.search(rf"\b{name}\b", text)), None
    )
    if exception_name:
        duration = re.search(
            r"\bafter\s+(\d{1,6}(?:\.\d{1,3})?)\s*(milliseconds?|ms|seconds?|secs?|s)\b",
            folded,
        )
        if duration and "timeout" in exception_name.casefold():
            unit = duration.group(2).lower()
            unit = "ms" if unit in {"ms", "millisecond", "milliseconds"} else "seconds"
            return f"{exception_name} after {duration.group(1)} {unit}"[:200], "error"
        return exception_name, "error"
    if re.search(r"\b(?:timed? ?out|timeout)\b", folded):
        duration = re.search(
            r"\bafter\s+(\d{1,6}(?:\.\d{1,3})?)\s*(milliseconds?|ms|seconds?|secs?|s)\b",
            folded,
        )
        message = "Timeout"
        if duration:
            unit = duration.group(2).lower()
            unit = "ms" if unit in {"ms", "millisecond", "milliseconds"} else "seconds"
            message = f"Timeout after {duration.group(1)} {unit}"
        return message[:200], "error"
    return "Other provider error", "error"


def _engine_failure_reason(errors: tuple[dict[str, str], ...]) -> str:
    """Classify provider diagnostics without exposing their raw text as a public error."""
    diagnostics = " ".join(item.get("reason", "") for item in errors).casefold()
    if "captcha" in diagnostics or "bot" in diagnostics:
        return "search_captcha"
    if "rate" in diagnostics or "429" in diagnostics or "too many" in diagnostics:
        return "search_rate_limited"
    return "search_provider_failure"
