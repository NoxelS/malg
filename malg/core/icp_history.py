"""Campaign-scoped durable identity ledger for ICP batch research.

This ledger is deliberately separate from semantic agent memory.  It provides
the exact uniqueness constraint that retrieval-based memory cannot guarantee.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from malg.core.models.icp import ICPIdentity, ICPResult


def default_history_path() -> Path:
    """Return the host-controlled path for the durable ICP history ledger."""
    directory = Path(os.environ.get("MALG_MEMORY_DIRECTORY", "/tmp/malg-memory"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "icp-history.sqlite"


class ICPHistoryLedger:
    """Atomically retain accepted ICP segment identities per campaign.

    The JSON payload is stored with the claim so a successful reservation can
    be recovered if presentation-file writing is interrupted after the commit.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Open or create the SQLite ledger at the supplied host-owned path."""
        self.path = path or default_history_path()
        self.connection = sqlite3.connect(self.path, timeout=5.0)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS accepted_icps (
                campaign_id TEXT NOT NULL,
                segment_key TEXT NOT NULL,
                icp_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                accepted_at TEXT NOT NULL,
                PRIMARY KEY (campaign_id, segment_key),
                UNIQUE (campaign_id, icp_id)
            )
            """
        )
        self.connection.commit()

    def accepted_identities(self, campaign_id: str, *, limit: int) -> list[ICPIdentity]:
        """Return oldest accepted identities as bounded agent exclusion cards."""
        rows = self.connection.execute(
            """
            SELECT payload_json FROM accepted_icps
            WHERE campaign_id = ?
            ORDER BY accepted_at, segment_key
            LIMIT ?
            """,
            (campaign_id, limit),
        ).fetchall()
        return [ICPResult.model_validate_json(row[0]).identity for row in rows]

    def claim(self, icp: ICPResult, *, run_id: str) -> bool:
        """Persist an ICP exactly once for its campaign identity.

        Returns false for a segment or ICP identifier already accepted for the
        campaign.  The database's unique constraints make this safe across
        fresh process starts and concurrent runners.
        """
        timestamp = datetime.now(UTC).isoformat()
        result = self.connection.execute(
            """
            INSERT OR IGNORE INTO accepted_icps (
                campaign_id, segment_key, icp_id, run_id, payload_json, accepted_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                icp.campaign_id,
                icp.identity.segment_key(),
                icp.icp_id,
                run_id,
                icp.model_dump_json(),
                timestamp,
            ),
        )
        self.connection.commit()
        return result.rowcount == 1

    def release(self, icp: ICPResult, *, run_id: str) -> None:
        """Release this run's claim when downstream durable storage fails.

        A caller may claim an ICP before handing it to another durable store.
        Removing only the matching claim keeps a failed store retryable without
        disturbing a claim made by another run.
        """
        self.connection.execute(
            """
            DELETE FROM accepted_icps
            WHERE campaign_id = ? AND segment_key = ? AND icp_id = ? AND run_id = ?
            """,
            (icp.campaign_id, icp.identity.segment_key(), icp.icp_id, run_id),
        )
        self.connection.commit()

    def close(self) -> None:
        """Close the SQLite handle after the host finishes the batch."""
        self.connection.close()
