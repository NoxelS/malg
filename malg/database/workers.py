"""Persistence operations for durable worker liveness."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from malg.database.models import WorkerHeartbeat


def record_worker_heartbeat(session: Session, worker_token: str, now: datetime) -> None:
    """Insert or update a worker heartbeat in the caller's transaction."""
    heartbeat = session.get(WorkerHeartbeat, worker_token)
    if heartbeat is None:
        session.add(WorkerHeartbeat(worker_token=worker_token, created_at=now, last_seen_at=now))
    else:
        heartbeat.last_seen_at = now
    session.flush()
