#!/usr/bin/env python3
"""Run a low-volume paired SearXNG comparison and save JSONL measurements."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class Query:
    """One public, non-sensitive benchmark query and its SearXNG language."""

    id: str
    language: str
    query: str


@dataclass(frozen=True)
class Measurement:
    """One endpoint attempt with bounded, non-content outcome metadata."""

    timestamp: str
    query_id: str
    language: str
    route: str
    latency_ms: float
    outcome: str
    http_status: int | None
    usable_results: int
    engines_returned: int
    unresponsive_engines: tuple[tuple[str, str], ...]
    error: str | None


def load_queries(path: Path) -> list[Query]:
    """Read JSONL queries, rejecting duplicates and malformed rows before traffic."""
    queries: list[Query] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on query line {line_number}") from exc
        if not isinstance(value, Mapping):
            raise ValueError(f"query line {line_number} must be an object")
        query_id, language, query = value.get("id"), value.get("language"), value.get("query")
        if not all(isinstance(item, str) and item.strip() for item in (query_id, language, query)):
            raise ValueError(f"query line {line_number} needs non-empty id, language, and query")
        assert isinstance(query_id, str) and isinstance(language, str) and isinstance(query, str)
        if query_id in seen_ids:
            raise ValueError(f"duplicate query id {query_id!r}")
        if len(query) > 300:
            raise ValueError(f"query {query_id!r} exceeds 300 characters")
        seen_ids.add(query_id)
        queries.append(Query(query_id, language, query))
    if not queries:
        raise ValueError("query file contains no queries")
    return queries


def _engine_failures(payload: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    raw = payload.get("unresponsive_engines", [])
    failures: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for value in raw:
            if isinstance(value, list) and len(value) >= 2:
                name = str(value[0])[:80]
                reason = str(value[1]).lower()
                if "429" in reason or "rate" in reason:
                    category = "rate_limit"
                elif "captcha" in reason:
                    category = "captcha"
                else:
                    category = "other"
                failures.append((name, category))
    return tuple(failures)


def measure(client: httpx.Client, base_url: str, route: str, query: Query) -> Measurement:
    """Perform one search and return counts/categories without storing result text."""
    started = time.monotonic()
    timestamp = datetime.now(UTC).isoformat()
    try:
        response = client.get(
            f"{base_url.rstrip('/')}/search",
            params={"q": query.query, "format": "json", "language": query.language},
        )
        latency = round((time.monotonic() - started) * 1000, 2)
        status = response.status_code
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
            return Measurement(
                timestamp,
                query.id,
                query.language,
                route,
                latency,
                "invalid_response",
                status,
                0,
                0,
                (),
                None,
            )
        results = payload["results"]
        usable = sum(
            isinstance(item, Mapping)
            and isinstance(item.get("url"), str)
            and urlparse(item["url"]).scheme in {"http", "https"}
            for item in results
        )
        return Measurement(
            timestamp,
            query.id,
            query.language,
            route,
            latency,
            "usable" if usable else "empty",
            status,
            usable,
            len(
                {
                    engine
                    for item in results
                    if isinstance(item, Mapping) and isinstance(item.get("engines"), list)
                    for engine in item["engines"]
                    if isinstance(engine, str)
                }
            ),
            _engine_failures(payload), None,
        )
    except httpx.HTTPStatusError as exc:
        latency = round((time.monotonic() - started) * 1000, 2)
        status = exc.response.status_code
        outcome = "rate_limit" if status == 429 else "http_error"
        return Measurement(
            timestamp, query.id, query.language, route, latency, outcome, status, 0, 0, (), None
        )
    except (httpx.HTTPError, ValueError) as exc:
        latency = round((time.monotonic() - started) * 1000, 2)
        return Measurement(
            timestamp,
            query.id,
            query.language,
            route,
            latency,
            "transport_error",
            None,
            0,
            0,
            (),
            type(exc).__name__,
        )


def run(args: argparse.Namespace) -> int:
    """Run each query against both routes once, in alternating route order."""
    queries = load_queries(args.queries)
    endpoints = [("direct", args.direct_url), ("tor", args.tor_url)]
    if args.first_route == "tor":
        endpoints.reverse()
    completed: set[tuple[str, str]] = set()
    if args.output.exists():
        for line_number, line in enumerate(args.output.read_text(encoding="utf-8").splitlines(), 1):
            try:
                prior = json.loads(line)
                completed.add((prior["query_id"], prior["route"]))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(f"invalid measurement on output line {line_number}") from exc
    pending = [query for query in queries if any((query.id, route) not in completed for route, _ in endpoints)]
    if not pending:
        print("All query and route pairs are already present in the output; no requests sent.")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = args.output.open("a", encoding="utf-8")
    try:
        with httpx.Client(timeout=args.timeout_seconds) as client:
            interval = args.window_hours * 3600 / len(pending)
            for index, query in enumerate(pending):
                pair_started = time.monotonic()
                pair = endpoints if index % 2 == 0 else endpoints[::-1]
                for route, url in pair:
                    if (query.id, route) in completed:
                        continue
                    result = measure(client, url, route, query)
                    output.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
                    output.flush()
                    print(
                        f"{result.query_id} {route}: {result.outcome}, "
                        f"{result.usable_results} usable, {result.latency_ms} ms"
                    )
                    completed.add((query.id, route))
                if index < len(pending) - 1:
                    time.sleep(max(0, interval - (time.monotonic() - pair_started)))
    finally:
        output.close()
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse safe, explicit benchmark inputs; require both private endpoints."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct-url", required=True, help="private direct SearXNG base URL")
    parser.add_argument("--tor-url", required=True, help="private Tor-backed SearXNG base URL")
    parser.add_argument("--queries", type=Path, required=True, help="JSONL query set")
    parser.add_argument("--output", type=Path, required=True, help="append-only JSONL output file")
    parser.add_argument("--window-hours", type=float, default=24.0)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--first-route", choices=("direct", "tor"), default="direct")
    args = parser.parse_args(argv)
    if args.window_hours <= 0 or args.timeout_seconds <= 0:
        parser.error("window and timeout values must be positive")
    return args


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
