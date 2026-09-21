"""Process boundary for stopping synchronous generated research work."""

from __future__ import annotations

import asyncio
import multiprocessing
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ExecutionConfig:
    """Serializable limits used by the parent supervisor."""

    initialization_timeout_seconds: float = 20
    cleanup_reserve_seconds: float = 5


def _child_entry(entrypoint: Callable[[], None], phase_pipe: int) -> None:
    """Run a stage in a fresh process; only lifecycle notifications cross the pipe."""
    del phase_pipe
    entrypoint()


async def run_supervised(
    entrypoint: Callable[[], None], *, deadline_at: datetime, config: ExecutionConfig | None = None
) -> None:
    """Run one stage process and terminate it before deadline cleanup reserve."""
    limits = config or ExecutionConfig()
    if deadline_at.tzinfo is None:
        raise ValueError("deadline_at must be timezone-aware")
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_child_entry, args=(entrypoint, -1), daemon=True)
    process.start()
    try:
        while process.is_alive():
            remaining = (deadline_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()
            if remaining <= limits.cleanup_reserve_seconds:
                process.terminate()
                await asyncio.to_thread(process.join, 2)
                if process.is_alive():
                    process.kill()
                    await asyncio.to_thread(process.join, 2)
                raise TimeoutError("research child exceeded its fenced deadline")
            await asyncio.sleep(min(0.25, remaining))
        if process.exitcode:
            raise RuntimeError(f"research child exited with status {process.exitcode}")
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 2)
