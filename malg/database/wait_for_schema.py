"""Read-only gate that waits for the required Alembic revision."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from malg.database.session import database_url


def required_revision() -> str:
    """Return the single migration head bundled with this application image.

    ``ALEMBIC_CONFIG`` may point to an explicit configuration file.  Source
    checkouts default to the repository configuration.  Configuration and
    migration graph errors are raised immediately, before database polling.
    """
    config_path = os.environ.get("ALEMBIC_CONFIG")
    path = Path(config_path) if config_path else Path(__file__).resolve().parents[2] / "alembic.ini"
    script = ScriptDirectory.from_config(Config(str(path)))
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one Alembic head, found {len(heads)}")
    return heads[0]


def current_revision(url: str | None = None) -> str | None:
    """Return the revision only when exactly one Alembic row is present."""
    engine = create_engine(url or database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            values = connection.scalars(text("SELECT version_num FROM alembic_version")).all()
            return str(values[0]) if len(values) == 1 else None
    finally:
        engine.dispose()


def wait_for_schema(timeout_seconds: int = 300) -> bool:
    """Poll once per second until the exact bundled revision is available."""
    revision = required_revision()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if current_revision() == revision:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main() -> int:
    """Run the non-mutating schema gate for API and worker startup."""
    parser = argparse.ArgumentParser(prog="python -m malg.database.wait_for_schema")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    return 0 if wait_for_schema(args.timeout_seconds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
