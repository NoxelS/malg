"""Shared durable discovery caching with short transactions and fenced leases."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from malg.database.models import SearchCacheEntry
from malg.database.session import database_url, make_engine, make_session_factory


@dataclass(frozen=True)
class CacheLookup:
    """Outcome of a cache lookup or lease acquisition."""

    state: Literal["hit", "owner", "wait", "failed"]
    payload: dict[str, Any] | None = None
    token: str | None = None


class SearchCache:
    """Persist every successful response across worker processes and runs.

    Transactions never span HTTP requests. Leases expire after worker loss;
    publication checks the token so an obsolete owner cannot overwrite a refresh.
    Expiry controls reuse, not retention. No size eviction discards stored queries.
    """

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        """Use the supplied session factory without creating or modifying schema."""
        self._sessions = sessions

    @staticmethod
    def key(request: dict[str, Any]) -> str:
        """Hash versioned request identity without exposing queries in logs."""
        return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()

    def lookup(self, request: dict[str, Any], *, lease_seconds: float) -> CacheLookup:
        """Return a fresh result, claim its refresh, or ask a follower to wait.

        A database clock determines freshness and lease expiry for all workers.
        Schema/connection errors propagate: bypassing coordination would duplicate traffic.
        """
        key = self.key(request)
        with self._sessions() as session:
            if session.get(SearchCacheEntry, key) is None:
                try:
                    with session.begin_nested():
                        session.add(SearchCacheEntry(cache_key=key, request=request))
                        session.flush()
                except IntegrityError:
                    pass  # A concurrent worker inserted the same query.
            now = session.scalar(select(func.current_timestamp()))
            assert isinstance(now, datetime)
            now = now.replace(tzinfo=UTC) if now.tzinfo is None else now
            row = session.get(SearchCacheEntry, key, populate_existing=True)
            assert row is not None
            expires = row.expires_at
            if expires is not None:
                expires = expires.replace(tzinfo=UTC) if expires.tzinfo is None else expires
            if row.response is not None and expires is not None and expires > now:
                session.execute(
                    update(SearchCacheEntry)
                    .execution_options(synchronize_session=False)
                    .where(SearchCacheEntry.cache_key == key)
                    .values(cache_hits=SearchCacheEntry.cache_hits + 1)
                )
                session.commit()
                return CacheLookup("hit", payload=row.response)
            failure = row.failure_until
            if failure is not None:
                failure = failure.replace(tzinfo=UTC) if failure.tzinfo is None else failure
            if failure is not None and failure > now:
                session.commit()
                return CacheLookup("failed")
            token = str(uuid4())
            claimed = session.execute(
                update(SearchCacheEntry)
                .execution_options(synchronize_session=False)
                .where(
                    SearchCacheEntry.cache_key == key,
                    or_(
                        SearchCacheEntry.lease_token.is_(None),
                        SearchCacheEntry.lease_expires_at <= now,
                    ),
                    or_(SearchCacheEntry.expires_at.is_(None), SearchCacheEntry.expires_at <= now),
                    or_(
                        SearchCacheEntry.failure_until.is_(None),
                        SearchCacheEntry.failure_until <= now,
                    ),
                )
                .values(lease_token=token, lease_expires_at=now + timedelta(seconds=lease_seconds))
                .returning(SearchCacheEntry.cache_key)
            ).scalar_one_or_none()
            session.commit()
            return CacheLookup("owner", token=token) if claimed is not None else CacheLookup("wait")

    def count(self, key: str, kind: Literal["upstream_requests", "coalesced_requests"]) -> None:
        """Atomically increment a durable counter for an actual request or waiting caller."""
        column = getattr(SearchCacheEntry, kind)
        with self._sessions.begin() as session:
            session.execute(
                update(SearchCacheEntry)
                .execution_options(synchronize_session=False)
                .where(SearchCacheEntry.cache_key == key)
                .values({kind: column + 1})
            )

    def finish(self, key: str, token: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        """Publish the complete successful JSON response only while owning its lease."""
        with self._sessions.begin() as session:
            now = session.scalar(select(func.current_timestamp()))
            assert isinstance(now, datetime)
            session.execute(
                update(SearchCacheEntry)
                .execution_options(synchronize_session=False)
                .where(
                    SearchCacheEntry.cache_key == key,
                    SearchCacheEntry.lease_token == token,
                    SearchCacheEntry.lease_expires_at > now,
                )
                .values(
                    response=payload,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                    lease_token=None,
                    lease_expires_at=None,
                    failure_until=None,
                )
            )

    def release(self, key: str, token: str, *, failed: bool) -> None:
        """Release a lease; briefly share provider failure with concurrent followers."""
        with self._sessions.begin() as session:
            now = session.scalar(select(func.current_timestamp()))
            assert isinstance(now, datetime)
            session.execute(
                update(SearchCacheEntry)
                .execution_options(synchronize_session=False)
                .where(
                    SearchCacheEntry.cache_key == key,
                    SearchCacheEntry.lease_token == token,
                )
                .values(
                    lease_token=None,
                    lease_expires_at=None,
                    failure_until=now + timedelta(seconds=2) if failed else None,
                )
            )


@lru_cache(maxsize=1)
def _configured_cache(url: str) -> SearchCache:
    return SearchCache(make_session_factory(make_engine(url)))


def default_search_cache() -> SearchCache:
    """Reuse a process-local connection pool targeting the shared configured database."""
    return _configured_cache(database_url())
