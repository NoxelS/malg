"""Authenticated command-line client for the MALG jobs API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from pydantic import TypeAdapter

from malg.core.models.jobs import ResearchJobRequest


def main() -> int:
    """Submit and inspect jobs without direct database or agent access."""
    parser = argparse.ArgumentParser(prog="malg")
    jobs = parser.add_subparsers(dest="group", required=True).add_parser("jobs")
    actions = jobs.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    get = actions.add_parser("get")
    get.add_argument("job_id", type=UUID)
    submit = actions.add_parser("submit")
    submit.add_argument("--request-file", required=True, type=Path)
    retry = actions.add_parser("retry")
    retry.add_argument("job_id", type=UUID)
    args = parser.parse_args()
    try:
        settings = _api_settings()
        if args.action == "list":
            method, path, payload = "GET", "/api/v1/jobs", None
        elif args.action == "get":
            method, path, payload = "GET", f"/api/v1/jobs/{args.job_id}", None
        elif args.action == "retry":
            method, path, payload = "POST", f"/api/v1/jobs/{args.job_id}/retry", None
        else:
            request: ResearchJobRequest = TypeAdapter(ResearchJobRequest).validate_json(
                args.request_file.read_text(encoding="utf-8")
            )
            payload = request.model_dump(mode="json")
            method, path = "POST", "/api/v1/jobs"
        response = httpx.request(
            method,
            f"{settings['url']}{path}",
            headers={"Authorization": f"Bearer {settings['token']}"},
            json=payload,
            timeout=30,
        )
    except (OSError, ValueError, httpx.HTTPError) as error:
        print(
            json.dumps(
                {
                    "error": "api_transport"
                    if isinstance(error, httpx.HTTPError)
                    else "invalid_input"
                }
            ),
            file=sys.stderr,
        )
        return 1 if isinstance(error, httpx.HTTPError) else 2
    try:
        output: Any = response.json()
    except ValueError:
        print(json.dumps({"error": "invalid_api_response"}))
        return 1
    print(json.dumps(output, sort_keys=True))
    if 200 <= response.status_code < 300:
        return 0
    if response.status_code in {401, 403} or response.status_code >= 500:
        return 1
    return 2


def _api_settings() -> dict[str, str]:
    """Read API endpoint and token only from environment variables."""
    url = os.environ.get("MALG_API_URL", "").rstrip("/")
    token = os.environ.get("MALG_API_TOKEN", "")
    if not url or not token:
        raise ValueError("MALG_API_URL and MALG_API_TOKEN are required")
    return {"url": url, "token": token}


if __name__ == "__main__":
    raise SystemExit(main())
