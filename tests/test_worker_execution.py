"""A revoked or uninitialized child cannot outlive its supervisor."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from multiprocessing import get_context
from pathlib import Path

import pytest

from malg.worker.execution import (
    ExecutionConfig,
    ResearchDeadlineExceeded,
    ResearchExecutionError,
    run_supervised,
)


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
        with pytest.raises(ResearchDeadlineExceeded, match="initialization") as timeout:
            await run_supervised(
                partial(_delayed_write, path, initialize=False),
                deadline_at=datetime.now(UTC) + timedelta(seconds=10),
                config=ExecutionConfig(0.2, 0),
            )
        assert timeout.value.reason_code == "initialization_timeout"
        await asyncio.sleep(2.1)
        assert not await asyncio.to_thread(Path(path).exists)

    asyncio.run(run())


class GenerationError(Exception):
    """Serializable third-party generation boundary error for a spawned child."""


def _failed_stage(connection, error_type: str) -> None:
    connection.send_bytes(b"initialized")
    if error_type == "search":
        from malg.core.web_search import SearchUnavailable

        raise SearchUnavailable("private upstream payload")
    if error_type == "rate_limit":
        raise GenerationError("RateLimitError: private upstream payload")
    raise RuntimeError("private upstream payload")


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        ("rate_limit", "llm_rate_limited"),
        ("other", "research_execution_failed"),
        ("search", "search_unavailable"),
    ],
)
def test_child_failure_exposes_only_safe_public_code(error_type, code) -> None:
    with pytest.raises(ResearchExecutionError) as failure:
        asyncio.run(
            run_supervised(
                partial(_failed_stage, error_type=error_type),
                deadline_at=datetime.now(UTC) + timedelta(seconds=10),
                config=ExecutionConfig(5, 0),
            )
        )
    assert failure.value.code == code
    assert str(failure.value) == code
