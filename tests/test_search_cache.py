"""Durable cache leases, process sharing, and migration lifecycle outcomes."""

from __future__ import annotations

import importlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select, update

from malg.database.models import SearchCacheEntry
from malg.database.search_cache import SearchCache
from malg.database.session import make_session_factory


def test_crashed_owner_is_replaced_and_old_token_cannot_publish(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'lease.db'}")
    SearchCacheEntry.__table__.create(engine)
    sessions = make_session_factory(engine)
    cache = SearchCache(sessions)
    request = {"query": "lease"}
    first = cache.lookup(request, lease_seconds=30)
    assert first.state == "owner"
    assert first.token is not None
    assert cache.lookup(request, lease_seconds=30).state == "wait"
    with sessions.begin() as session:
        session.execute(
            update(SearchCacheEntry).values(
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=10)
            )
        )
    replacement = SearchCache(sessions).lookup(request, lease_seconds=30)
    assert replacement.state == "owner"
    assert replacement.token is not None
    cache.finish(cache.key(request), first.token, {"results": ["obsolete"]}, 300)
    cache.release(cache.key(request), first.token, failed=True)
    cache.finish(cache.key(request), replacement.token, {"results": ["current"]}, 300)
    assert cache.lookup(request, lease_seconds=30).payload == {"results": ["current"]}
    engine.dispose()


def test_cache_migration_round_trip(tmp_path) -> None:
    migration = importlib.import_module("migrations.versions.20261006_01_search_cache")
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        assert "search_cache" in inspect(connection).get_table_names()
        columns = {column["name"] for column in inspect(connection).get_columns("search_cache")}
        assert columns == set(SearchCacheEntry.__table__.columns.keys())
        migration.downgrade()
        assert "search_cache" not in inspect(connection).get_table_names()
    engine.dispose()


def test_independent_worker_processes_share_one_response(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'workers.db'}"
    engine = create_engine(url)
    SearchCacheEntry.__table__.create(engine)
    script = """
import asyncio, sys, httpx
from sqlalchemy import create_engine
from malg.config import SearchConfig
from malg.core import web_search
from malg.database.search_cache import SearchCache
from malg.database.session import make_session_factory

async def respond(request):
    await asyncio.sleep(0.3)
    return httpx.Response(200, json={"results": [{"url": "https://example.test/shared"}]})

original_client = httpx.AsyncClient
web_search.httpx.AsyncClient = lambda **kwargs: original_client(
    **kwargs, transport=httpx.MockTransport(respond))
engine = create_engine(sys.argv[1])
config = SearchConfig(True, "http://search:8080", 5, 2, 2, 0, ("en",), ("general",))
client = web_search.SearxngSearchClient(config, cache=SearchCache(make_session_factory(engine)))
assert asyncio.run(client.search("shared"))[0].url == "https://example.test/shared"
engine.dispose()
"""
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", script, url], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for _ in range(2)
    ]
    try:
        for worker in workers:
            _, stderr = worker.communicate(timeout=30)
            assert worker.returncode == 0, stderr.decode()
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()
    with make_session_factory(engine)() as session:
        row = session.scalar(select(SearchCacheEntry))
        assert row is not None
        assert row.upstream_requests == 1
        assert row.cache_hits == 1
    engine.dispose()
