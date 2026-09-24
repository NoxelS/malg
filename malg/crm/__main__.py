"""Command-line entry point for authenticated Twenty contract operations."""

from __future__ import annotations

import argparse
import asyncio
import json

from malg.config import get_twenty_schema_config, load_settings
from malg.crm.client import TwentyClient
from malg.crm.schema import (
    SchemaConflict,
    contract_hash,
    inspect_remote,
    load_manifest,
    reconcile,
)


def main() -> int:
    """Inspect, plan, check, or apply the additive remote contract."""
    parser = argparse.ArgumentParser(prog="python -m malg.crm")
    subparsers = parser.add_subparsers(dest="group", required=True)
    schema = subparsers.add_parser("schema")
    schema.add_argument("action", choices=("inspect", "plan", "check", "apply"))
    args = parser.parse_args()
    try:
        result = asyncio.run(_run_schema(args.action))
    except SchemaConflict as error:
        print(json.dumps({"schema_compatible": False, "reason": str(error)}, sort_keys=True))
        return 2
    except Exception:
        print(json.dumps({"schema_compatible": False, "reason": "crm_unavailable"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    if args.action == "check" and not result.get("schema_compatible", False):
        return 2
    return 0


async def _run_schema(action: str) -> dict[str, object]:
    """Run one schema command using the schema-scoped credential."""
    client = TwentyClient(get_twenty_schema_config(load_settings()))
    try:
        manifest = load_manifest()
        if action == "inspect":
            remote = await inspect_remote(client)
            return {
                "contract_hash": contract_hash(manifest),
                "contract_version": manifest["contract_version"],
                "objects": remote,
            }
        operations = await reconcile(client, manifest, apply=action == "apply")
        return {
            "contract_hash": contract_hash(manifest),
            "contract_version": manifest["contract_version"],
            "operations": operations,
            "schema_compatible": True if action == "apply" else not operations,
        }
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(main())
