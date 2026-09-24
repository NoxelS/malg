"""Host-owned HTTP retrieval and source-safe search observations."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from http.client import HTTPMessage
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from malg.core.budget import reserve_external_attempt
from malg.core.web_search import SearchResult, SearxngSearchClient


@dataclass(frozen=True)
class FetchObservation:
    """Bounded result of one safe HTTP fetch."""

    url: str
    final_url: str | None
    status_code: int | None
    outcome: str
    content_type: str | None = None
    content_hash: str | None = None
    text: str = ""
    excerpts: tuple[dict[str, object], ...] = ()
    reason_code: str | None = None


@dataclass(frozen=True)
class SearchResponse:
    """Search results plus engine diagnostics; empty results are not health proof."""

    results: tuple[SearchResult, ...]
    engine_errors: tuple[dict[str, str], ...] = ()
    health: str = "unknown"
    query_id: str | None = None


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "form", "nav", "noscript"}:
            self._hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "form", "nav", "noscript"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden and data.strip():
            self.parts.append(data.strip())


class RetrievalService:
    """Retrieve only public HTTP(S) pages with strict size and redirect bounds."""

    def __init__(self, search_client: SearxngSearchClient | None = None) -> None:
        self.search_client = search_client
        self._cache: dict[str, FetchObservation] = {}
        self._query_cache: dict[tuple[str, str], SearchResponse] = {}

    async def search(self, query: str, *, language: str = "en") -> SearchResponse:
        """Return SearchResponse with ``.results`` containing title/url/snippet attributes.

        Use ``response.results`` and ``hit.url``, not dictionary indexing or vars().
        Snippets are discovery clues, never host-verified evidence. Repeated normalized
        query/language pairs reuse the cached response without another request.
        """
        normalized = " ".join(query.split()).casefold()
        key = (normalized, language)
        if key in self._query_cache:
            return self._query_cache[key]
        if self.search_client is None:
            response = SearchResponse((), health="unavailable")
        else:
            results = tuple(await self.search_client.search(query, language=language))
            response = SearchResponse(results, health="unknown")
        self._query_cache[key] = response
        return response

    async def fetch(self, url: str, *, purpose: str) -> FetchObservation:
        """Fetch public evidence; use ``page.excerpts`` for host-known quote references.

        Call with ``purpose="evidence"``. The FetchObservation result exposes outcome,
        text, final_url and excerpts; each excerpt is a dict with ``id`` and ``text``.
        Only quote exact text from those excerpts. Empty excerpts mean no usable
        evidence. LinkedIn and its shortener are prohibited destinations; their
        identifiers must come from other public sources. Unsafe, blocked and
        unavailable pages return typed observations; an exhausted request budget
        raises BudgetExhausted.
        """
        del purpose
        canonical = canonicalize_url(url)
        if canonical is None:
            return FetchObservation(
                url, None, None, "unsafe_url", reason_code="invalid_or_disallowed_url"
            )
        cached = self._cache.get(canonical)
        if cached is not None:
            return cached
        reserve_external_attempt("fetch")
        observation = await asyncio.to_thread(_fetch_url, canonical)
        self._cache[canonical] = observation
        return observation

    @property
    def observations(self) -> tuple[FetchObservation, ...]:
        """Return host-fetched evidence for quote validation and durable checkpoints."""
        return tuple(self._cache.values())


def canonicalize_url(url: str) -> str | None:
    """Normalize public-research URLs, rejecting credentials, unsafe ports and LinkedIn."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password:
            return None
        if parsed.port not in {None, 80, 443} or not parsed.hostname:
            return None
        host = parsed.hostname.casefold().rstrip(".")
        policy_host = unquote(host).encode("idna").decode("ascii").casefold().rstrip(".")
        if policy_host in {"linkedin.com", "lnkd.in"} or policy_host.endswith(
            (".linkedin.com", ".lnkd.in")
        ):
            return None
        path = parsed.path or "/"
        query = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.casefold()
            not in {
                "utm_source",
                "utm_medium",
                "utm_campaign",
                "utm_term",
                "utm_content",
                "gclid",
                "fbclid",
            }
        ]
        return urlunsplit((parsed.scheme.lower(), host, path, urlencode(query), ""))
    except ValueError:
        return None


def _public_host(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(addresses) and all(
        not (
            ipaddress.ip_address(item[4][0]).is_private
            or ipaddress.ip_address(item[4][0]).is_loopback
            or ipaddress.ip_address(item[4][0]).is_link_local
        )
        for item in addresses
    )


class _UnsafeRedirect(ValueError):
    """An observed redirect would leave the public URL boundary."""


class _PublicRedirectHandler(HTTPRedirectHandler):
    """Recheck each redirect before urllib can connect to its destination."""

    max_redirections = 3

    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> Request | None:
        """Reject non-public redirect targets and reserve each new fetch attempt."""
        canonical = canonicalize_url(newurl)
        if canonical is None or not _public_host(urlsplit(canonical).hostname or ""):
            fp.close()
            raise _UnsafeRedirect(
                "invalid_or_disallowed_url" if canonical is None else "private_or_unresolved_host"
            )
        reserve_external_attempt("fetch")
        return super().redirect_request(req, fp, code, msg, headers, canonical)


def _fetch_url(url: str) -> FetchObservation:
    parsed = urlsplit(url)
    if not _public_host(parsed.hostname or ""):
        return FetchObservation(
            url, None, None, "unsafe_url", reason_code="private_or_unresolved_host"
        )
    try:
        request = Request(
            url, headers={"Accept-Encoding": "identity", "User-Agent": "malg-research/1"}
        )
        with build_opener(_PublicRedirectHandler()).open(request, timeout=25) as response:
            status = int(response.status)
            final = canonicalize_url(response.geturl()) or response.geturl()
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                return FetchObservation(
                    url, final, status, "unsupported_content", content_type=content_type
                )
            body = response.read(1_048_577)
            if len(body) > 1_048_576:
                return FetchObservation(url, final, status, "too_large", content_type=content_type)
            text = body.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            parser = _VisibleText()
            parser.feed(text)
            visible = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:20_000]
            digest = hashlib.sha256(body).hexdigest()
            excerpts: tuple[dict[str, object], ...] = tuple(
                {"id": f"{digest}:{offset}", "text": visible[offset : offset + 2000]}
                for offset in range(0, min(len(visible), 20_000), 2000)
            )
            return FetchObservation(
                url,
                final,
                status,
                "available" if 200 <= status < 400 else "blocked",
                content_type,
                digest,
                visible,
                excerpts,
            )
    except _UnsafeRedirect as error:
        return FetchObservation(url, None, None, "unsafe_url", reason_code=str(error))
    except HTTPError as error:
        outcome = "blocked" if error.code in {403, 408, 429, 999, 504, 524} else "unavailable"
        return FetchObservation(
            url, error.geturl(), int(error.code), outcome, reason_code="http_error"
        )
    except (OSError, ValueError, UnicodeError) as error:
        return FetchObservation(url, None, None, "unavailable", reason_code=type(error).__name__)
