"""Conservative identity normalization and deterministic Twenty IDs."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid5


def normalize_domain(value: str) -> str:
    """Normalize an HTTP(S) URL or host without collapsing subdomains."""
    candidate = value.strip()
    parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("official website must be an HTTP(S) URL or host")
    host = parsed.hostname.encode("idna").decode("ascii").lower().removeprefix("www.")
    port = parsed.port
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    return host


def normalize_linkedin(value: str, *, person: bool) -> str:
    """Normalize an observed company or person LinkedIn URL."""
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    prefix = "/in/" if person else "/company/"
    if (
        parsed.scheme.lower() != "https"
        or host != "linkedin.com"
        or not parsed.path.startswith(prefix)
    ):
        raise ValueError("LinkedIn URL is not an observed URL for this record kind")
    path = "/" + "/".join(part for part in parsed.path.split("/") if part) + "/"
    return urlunsplit(("https", "linkedin.com", path, "", ""))


def deterministic_id(workspace_id: str, object_name: str, identity: str) -> UUID:
    """Derive the only permitted host-selected UUID for a new record."""
    seed = f"https://twenty.noel.fyi/malg/{workspace_id}/{object_name}/{identity}"
    return uuid5(NAMESPACE_URL, seed)
