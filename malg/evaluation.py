"""Deterministic evaluation manifest runner without live client initialization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def run_offline(manifest: Path, max_cases: int | None = None) -> dict[str, Any]:
    """Load labeled cases and return a transparent, non-inferred report."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    cases_value = data.get("cases", data) if isinstance(data, dict) else data
    cases = cases_value
    if not isinstance(cases, list):
        raise ValueError("evaluation manifest must contain a cases list")
    selected = cases[:max_cases] if max_cases is not None else cases
    return {"mode": "offline", "cases": selected, "count": len(selected), "outbound_requests": 0}


def main() -> None:
    """Run the explicitly requested offline evaluation mode."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "live"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "live":
        raise SystemExit("live evaluation requires the isolated sandbox runner")
    report = run_offline(args.manifest, args.max_cases)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
