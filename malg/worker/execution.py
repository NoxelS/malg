"""Spawn isolation and bounded termination for generated research stages."""

from __future__ import annotations

import asyncio
import multiprocessing
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing.connection import Connection


@dataclass(frozen=True)
class ExecutionConfig:
    """Parent-enforced initialization and shutdown reserves in seconds."""

    initialization_timeout_seconds: float = 20
    cleanup_reserve_seconds: float = 5


class ResearchDeadlineExceeded(TimeoutError):
    """A supervisor-enforced timeout with a safe cause independent of model output."""

    def __init__(self, message: str, reason_code: str) -> None:
        """Retain whether initialization or stage execution reached its deadline."""
        super().__init__(message)
        self.reason_code = reason_code


class ResearchExecutionError(RuntimeError):
    """A child failure carrying only a host-allowlisted public reason code."""

    codes = frozenset(
        {
            "llm_rate_limited",
            "evidence_storage_failed",
            "research_execution_failed",
            "search_unavailable",
        }
    )

    def __init__(self, code: str) -> None:
        """Replace unknown codes with the generic failure; never expose child payloads."""
        self.code = code if code in self.codes else "research_execution_failed"
        super().__init__(self.code)


def _child_entry(entrypoint: Callable[[Connection], None], initialized: Connection) -> None:
    """Run a picklable stage; the stage signals after its resources initialize."""
    try:
        entrypoint(initialized)
    except Exception as error:
        code = "research_execution_failed"
        if type(error).__name__ == "GenerationError" and "RateLimitError" in str(error):
            code = "llm_rate_limited"
        elif type(error).__name__ == "SearchUnavailable":
            code = "search_unavailable"
        elif type(error).__name__ == "DataError":
            code = "evidence_storage_failed"
        with suppress(OSError):
            initialized.send_bytes(f"failed:{code}".encode("ascii"))
        raise
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
        raise ResearchDeadlineExceeded(
            "research deadline already exhausted", "stage_deadline_exhausted"
        )
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_child_entry, args=(entrypoint, sender), daemon=True)
    process.start()
    sender.close()
    initialized = False
    failure_code: str | None = None
    init_deadline = asyncio.get_running_loop().time() + limits.initialization_timeout_seconds
    try:
        while True:
            if receiver.poll():
                try:
                    message = receiver.recv_bytes(128)
                except EOFError:
                    message = b""
                if message == b"initialized":
                    initialized = True
                elif message.startswith(b"failed:"):
                    failure_code = message[7:].decode("ascii", errors="replace")
                elif message:
                    raise ResearchExecutionError("research_execution_failed")
            if not process.is_alive():
                await asyncio.to_thread(process.join)
                # The child may send its final message between poll and exit.
                if failure_code is None and receiver.poll():
                    with suppress(EOFError):
                        message = receiver.recv_bytes(128)
                        if message.startswith(b"failed:"):
                            failure_code = message[7:].decode("ascii", errors="replace")
                if not initialized or process.exitcode:
                    raise ResearchExecutionError(failure_code or "research_execution_failed")
                return
            remaining = (deadline_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()
            if remaining <= limits.cleanup_reserve_seconds:
                raise ResearchDeadlineExceeded(
                    "research child exceeded its fenced deadline", "stage_deadline_exhausted"
                )
            if not initialized and asyncio.get_running_loop().time() >= init_deadline:
                raise ResearchDeadlineExceeded(
                    "research child initialization timed out", "initialization_timeout"
                )
            await asyncio.sleep(min(0.05, remaining))
    finally:
        receiver.close()
        await _terminate(process)
        process.close()
