"""A revoked or uninitialized child cannot outlive its supervisor."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from multiprocessing import get_context
from pathlib import Path

import pytest

from malg.worker.execution import ExecutionConfig, run_supervised


def _delayed_write(path: str, connection, *, initialize: bool, started=None) -> None:
    if initialize:
        connection.send_bytes(b"initialized")
        started.set()
    time.sleep(2)
    Path(path).write_text("stale side effect")


def test_cancellation_terminates_initialized_child(tmp_path):
    async def run():
        path = str(tmp_path / "result")
        started = get_context("spawn").Event()
        task = asyncio.create_task(
            run_supervised(
                partial(_delayed_write, path, initialize=True, started=started),
                deadline_at=datetime.now(UTC) + timedelta(seconds=10),
                config=ExecutionConfig(5, 0),
            )
        )
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(2.1)
        assert not await asyncio.to_thread(Path(path).exists)

    asyncio.run(run())


def test_initialization_timeout_terminates_silent_child(tmp_path):
    async def run():
        path = str(tmp_path / "result")
        with pytest.raises(TimeoutError, match="initialization"):
            await run_supervised(
                partial(_delayed_write, path, initialize=False),
                deadline_at=datetime.now(UTC) + timedelta(seconds=10),
                config=ExecutionConfig(0.2, 0),
            )
        await asyncio.sleep(2.1)
        assert not await asyncio.to_thread(Path(path).exists)

    asyncio.run(run())
