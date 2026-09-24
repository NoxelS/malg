"""Safe deterministic probes used before account validation.

The probes intentionally establish URL reachability and mail-domain routing only.
They never send a message or attempt SMTP recipient verification.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from pydantic import HttpUrl, TypeAdapter

from malg.core.budget import reserve_external_attempt
from malg.core.models.account import (
    AccountProbeReport,
    AccountResearchResult,
    CheckOutcome,
    MailDomainProbeResult,
    UrlProbeResult,
)
from malg.core.retrieval import canonicalize_url


def _checked_at() -> datetime:
    """Return an explicit UTC timestamp for probe observations."""
    return datetime.now(UTC)


def _is_public_host(hostname: str) -> bool:
    """Reject loopback, private, link-local, multicast, and reserved destinations."""
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False
    return bool(addresses)


class AccountProbeService:
    """Run bounded URL checks and conservative email-domain checks for a candidate."""

    def __init__(self, *, timeout_seconds: float = 10.0, max_redirects: int = 3) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_redirects = max_redirects

    async def probe_candidate(self, candidate: AccountResearchResult) -> AccountProbeReport:
        """Probe explicit candidate URLs and domains without side effects."""
        urls = self._candidate_urls(candidate)
        email_domains = self._email_domains(candidate)
        url_checks = [await self.probe_url(url) for url in urls]
        mail_domain_checks = [await self.probe_mail_domain(domain) for domain in email_domains]
        return AccountProbeReport(url_checks=url_checks, mail_domain_checks=mail_domain_checks)

    async def probe_url(self, value: HttpUrl) -> UrlProbeResult:
        """Recheck public-research URL policy and DNS on every bounded redirect."""
        checked_at = _checked_at()
        current_url = str(value)
        for _ in range(self._max_redirects + 1):
            canonical = canonicalize_url(current_url)
            if canonical is None:
                return UrlProbeResult(
                    url=value,
                    outcome=CheckOutcome.FAIL,
                    checked_at=checked_at,
                    reason="URL is not permitted for automated public research.",
                )
            current_url = canonical
            parsed = urlparse(current_url)
            is_public = await asyncio.to_thread(_is_public_host, parsed.hostname or "")
            if not is_public:
                return UrlProbeResult(
                    url=value,
                    outcome=CheckOutcome.FAIL,
                    checked_at=checked_at,
                    reason="URL resolves to a non-public or unavailable destination.",
                )
            try:
                async with httpx.AsyncClient(
                    follow_redirects=False,
                    timeout=httpx.Timeout(self._timeout_seconds, connect=5.0),
                ) as client:
                    reserve_external_attempt("fetch")
                    response = await client.get(
                        current_url, headers={"User-Agent": "MALG account probe"}
                    )
            except httpx.HTTPError as error:
                return UrlProbeResult(
                    url=value,
                    outcome=CheckOutcome.INCONCLUSIVE,
                    checked_at=checked_at,
                    reason=f"HTTP request failed: {type(error).__name__}.",
                )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return UrlProbeResult(
                        url=value,
                        outcome=CheckOutcome.FAIL,
                        status_code=response.status_code,
                        checked_at=checked_at,
                        reason="Redirect response did not provide a destination.",
                    )
                current_url = str(response.url.join(location))
                continue
            return UrlProbeResult(
                url=value,
                outcome=CheckOutcome.PASS
                if 200 <= response.status_code < 400
                else CheckOutcome.FAIL,
                status_code=response.status_code,
                final_url=TypeAdapter(HttpUrl).validate_python(str(response.url)),
                checked_at=checked_at,
                reason=None
                if 200 <= response.status_code < 400
                else "URL returned an error status.",
            )
        return UrlProbeResult(
            url=value,
            outcome=CheckOutcome.FAIL,
            checked_at=checked_at,
            reason="URL exceeded the redirect limit.",
        )

    async def probe_mail_domain(self, domain: str) -> MailDomainProbeResult:
        """Check domain resolution without claiming that a mailbox exists.

        The standard library cannot safely inspect MX and null-MX records. A
        resolved A/AAAA record only establishes that the domain exists, so the
        mail capability remains inconclusive until a configured DNS resolver is
        introduced in a later integration slice.
        """
        checked_at = _checked_at()
        reserve_external_attempt("fetch")
        try:
            await asyncio.to_thread(socket.getaddrinfo, domain, None, socket.AF_UNSPEC)
        except socket.gaierror:
            return MailDomainProbeResult(
                domain=domain,
                outcome=CheckOutcome.FAIL,
                accepts_mail=False,
                checked_at=checked_at,
                reason="Email domain does not resolve.",
            )
        return MailDomainProbeResult(
            domain=domain,
            outcome=CheckOutcome.INCONCLUSIVE,
            accepts_mail=None,
            checked_at=checked_at,
            reason="Domain resolves; MX or null-MX was not checked.",
        )

    @staticmethod
    def _candidate_urls(candidate: AccountResearchResult) -> list[HttpUrl]:
        """Collect official websites; LinkedIn identifiers are never probe destinations."""
        values: list[HttpUrl] = []
        for value in (
            candidate.identity.official_website if candidate.identity else None,
            candidate.data.website if candidate.data else None,
        ):
            if value is not None:
                values.append(value)
        return list(dict.fromkeys(values))

    @staticmethod
    def _email_domains(candidate: AccountResearchResult) -> list[str]:
        """Extract domains only from explicit sourced observations."""
        domains: list[str] = []
        for observation in candidate.observations:
            domains.extend(
                match.casefold()
                for match in re.findall(
                    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?[.][A-Za-z]{2,})",
                    observation.quote,
                )
            )
        return list(dict.fromkeys(domains))
