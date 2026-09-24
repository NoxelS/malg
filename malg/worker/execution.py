"""Spawn isolation and bounded termination for generated research stages."""

from __future__ import annotations

import asyncio
import multiprocessing
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing.connection import Connection


@dataclass(frozen=True)
class ExecutionConfig:
    """Parent-enforced initialization and shutdown reserves in seconds."""

    initialization_timeout_seconds: float = 20
    cleanup_reserve_seconds: float = 5


def _child_entry(entrypoint: Callable[[Connection], None], initialized: Connection) -> None:
    """Run a picklable stage; the stage signals after its resources initialize."""
    try:
        entrypoint(initialized)
    finally:
        initialized.close()


async def _terminate(process: multiprocessing.process.BaseProcess) -> None:
    """Reap the spawned child, escalating when cooperative termination fails."""
    if process.is_alive():
        process.terminate()
        await asyncio.to_thread(process.join, 2)
        if process.is_alive():
            process.kill()
    await asyncio.to_thread(process.join, 2)


async def run_supervised(
    entrypoint: Callable[[Connection], None],
    *,
    deadline_at: datetime,
    config: ExecutionConfig | None = None,
) -> None:
    """Run one isolated stage and terminate on cancellation, init timeout or deadline.

    The callable must contain only serializable arguments, never live database,
    transport or agent resources. It must send ``b"initialized"`` after initializing
    child-owned resources and persist its result before returning. Exit alone is
    not a result; callers must independently load the fenced checkpoint.
    """
    limits = config or ExecutionConfig()
    if deadline_at.tzinfo is None:
        raise ValueError("deadline_at must be timezone-aware")
    if limits.initialization_timeout_seconds <= 0:
        raise ValueError("initialization timeout must be positive")
    if limits.cleanup_reserve_seconds < 0:
        raise ValueError("cleanup reserve must be nonnegative")
    if (
        deadline_at.astimezone(UTC) - datetime.now(UTC)
    ).total_seconds() <= limits.cleanup_reserve_seconds:
        raise TimeoutError("research deadline already exhausted")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_child_entry, args=(entrypoint, sender), daemon=True)
    process.start()
    sender.close()
    initialized = False
    init_deadline = asyncio.get_running_loop().time() + limits.initialization_timeout_seconds
    try:
        while True:
            if not initialized and receiver.poll():
                try:
                    initialized = receiver.recv_bytes() == b"initialized"
                except EOFError as error:
                    raise RuntimeError("research child exited before initialization") from error
                if not initialized:
                    raise RuntimeError("invalid research initialization notification")
            if not process.is_alive():
                await asyncio.to_thread(process.join)
                if not initialized or process.exitcode:
                    raise RuntimeError(f"research child failed ({process.exitcode})")
                return
            remaining = (deadline_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()
            if remaining <= limits.cleanup_reserve_seconds:
                raise TimeoutError("research child exceeded its fenced deadline")
            if not initialized and asyncio.get_running_loop().time() >= init_deadline:
                raise TimeoutError("research child initialization timed out")
            await asyncio.sleep(min(0.05, remaining))
    finally:
        receiver.close()
        await _terminate(process)
        process.close()
