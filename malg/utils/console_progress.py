"""Safe, colorized console progress reporting for NOOA agent runs.

The observer runs in the application process, outside NOOA's generated-code
stdout capture. It remains visible in attached container logs while reporting
only bounded call metadata and result summaries.
"""

from __future__ import annotations

import inspect
import os
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, TextIO

_RESET = "\033[0m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_MAGENTA = "\033[35m"
_RED = "\033[31m"
_YELLOW = "\033[33m"


class ConsoleProgress:
    """Write timestamped NOOA lifecycle and MCP progress to a text stream.

    Args:
        stream: Destination for output. Standard output is the default, so
            Docker Compose forwards messages to the invoking terminal.
        clock: Monotonic clock used for durations; injectable for tests.
        timestamp: Local-clock formatter; injectable for tests.
        color: Enable ANSI colors. Colors are on by default unless ``NO_COLOR``
            is set, including for attached Docker container output.

    The reporter never renders prompts, generated code, argument values other
    than safe call identifiers, result bodies, or exception messages.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
        timestamp: Callable[[], str] = lambda: datetime.now().astimezone().strftime("%H:%M:%S"),
        *,
        color: bool = True,
    ) -> None:
        self._stream = stream or sys.stdout
        self._clock = clock
        self._timestamp = timestamp
        self._color = color and "NO_COLOR" not in os.environ
        self._llm_calls: dict[tuple[str, int], float] = {}
        self._mcp_calls: dict[str, float] = {}

    def attach(self, agent: Any) -> None:
        """Subscribe to agent calls and LLM turns without modifying NOOA tracing."""
        agent.event_manager.intercept("agent_call", self._agent_call)
        agent.event_manager.on("LLMCallStart", self._llm_call_start)
        agent.event_manager.on("LLMCallEnd", self._llm_call_end)
        attach_memory_progress = getattr(agent, "_set_memory_progress", None)
        if callable(attach_memory_progress):
            attach_memory_progress(self)

    def memory_saved(self, scope: str, memory_id: str) -> None:
        """Report a durable memory write without rendering its contents."""
        self._write("MEMORY", f"{scope} saved (id {_short_id(memory_id)})", _GREEN)

    def memory_loaded(self, scope: str, operation: str, count: int) -> None:
        """Report a memory lookup without rendering its query or results."""
        noun = "item" if count == 1 else "items"
        self._write("MEMORY", f"{scope} {operation} loaded {count} {noun}", _GREEN)

    def memory_updated(self, scope: str, memory_id: str, found: bool) -> None:
        """Report whether a memory refinement found its target."""
        outcome = "updated" if found else "not found"
        self._write("MEMORY", f"{scope} {outcome} (id {_short_id(memory_id)})", _GREEN)

    def memory_archived(self, scope: str, memory_id: str, found: bool) -> None:
        """Report whether a memory archive request found its target."""
        outcome = "archived" if found else "not found"
        self._write("MEMORY", f"{scope} {outcome} (id {_short_id(memory_id)})", _GREEN)

    def memory_associated(self, scope: str) -> None:
        """Report a memory-link write without rendering link endpoints."""
        self._write("MEMORY", f"{scope} associated memories", _GREEN)

    async def _agent_call(self, context: Any, next_call: Callable[[Any], Awaitable[Any]]) -> Any:
        label = _call_label(context.agent, context.method_name, context.args, context.kwargs)
        started = self._clock()
        self._write("AGENT", f"{label} started", _YELLOW)
        try:
            completed = await next_call(context)
        except Exception as error:
            self._write(
                "AGENT",
                f"{label} failed ({type(error).__name__}) in {self._elapsed(started)}",
                _RED,
            )
            raise
        self._write(
            "AGENT",
            f"{label} finished: {_result_summary(completed.result)} in {self._elapsed(started)}",
            _GREEN,
        )
        return completed

    def mcp_started(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Record and report an MCP call without rendering argument values."""
        call_id = f"{server_name}.{tool_name}:{id(arguments)}"
        self._mcp_calls[call_id] = self._clock()
        names = ", ".join(_argument_names(arguments)) or "none"
        self._write("MCP", f"{server_name}.{tool_name} started (args: {names})", _YELLOW)
        return call_id

    def mcp_finished(self, call_id: str, server_name: str, tool_name: str, result: Any) -> None:
        """Report a successful MCP call using bounded result metadata."""
        self._write(
            "MCP",
            f"{server_name}.{tool_name} finished: {_result_summary(result)} "
            f"in {self._duration(call_id, self._mcp_calls)}",
            _GREEN,
        )

    def mcp_failed(self, call_id: str, server_name: str, tool_name: str, error: Exception) -> None:
        """Report an MCP failure without leaking its message or chained details."""
        self._write(
            "MCP",
            f"{server_name}.{tool_name} failed ({type(error).__name__}) "
            f"in {self._duration(call_id, self._mcp_calls)}",
            _RED,
        )

    def _llm_call_start(self, event: Any) -> None:
        key = (event.generation_id, event.turn_number)
        self._llm_calls[key] = self._clock()
        self._write("LLM", f"{event.method_name} turn {event.turn_number} started", _MAGENTA)

    def _llm_call_end(self, event: Any) -> None:
        key = (event.generation_id, event.turn_number)
        outcome = "finished" if event.success else f"failed ({event.exception_type or 'Exception'})"
        self._write(
            "LLM",
            f"{event.method_name} turn {event.turn_number} {outcome} "
            f"in {self._duration(key, self._llm_calls)}",
            _GREEN if event.success else _RED,
        )

    def _elapsed(self, started: float) -> str:
        return f"{self._clock() - started:.1f}s"

    def _duration(self, key: Any, started: dict[Any, float]) -> str:
        began = started.pop(key, None)
        return "unknown time" if began is None else self._elapsed(began)

    def _write(self, category: str, message: str, color: str) -> None:
        timestamp = self._timestamp()
        if self._color:
            line = f"{_DIM}[{timestamp}]{_RESET} {color}{category:<5}{_RESET} {message}"
        else:
            line = f"[{timestamp}] {category:<5} {message}"
        print(line, file=self._stream, flush=True)


def _call_label(agent: Any, method_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    metadata = _call_metadata(agent, method_name, args, kwargs)
    return f"{method_name} ({metadata})" if metadata else method_name


def _call_metadata(
    agent: Any, method_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> str:
    try:
        bound = inspect.signature(getattr(agent, method_name)).bind_partial(*args, **kwargs)
    except (AttributeError, TypeError, ValueError):
        return ""
    details: list[str] = []
    for name, value in bound.arguments.items():
        if name in {"code", "keyword", "parameter", "lang", "agency", "region"} and isinstance(
            value, str
        ):
            details.append(f"{name}={_safe_identifier(value)}")
        elif name == "filter_pars" and isinstance(value, dict):
            details.append(f"filter dimensions={len(value)}")
        elif name in {"flags", "full", "reverse_time", "verbose", "top_n_accounts"} and isinstance(
            value, (bool, int)
        ):
            details.append(f"{name}={value}")
    return ", ".join(details)


def _safe_identifier(value: str) -> str:
    return repr(value[:80] + ("..." if len(value) > 80 else ""))


def _short_id(value: str) -> str:
    """Return a bounded identifier suitable for operational progress output."""
    return value[:8]


def _argument_names(arguments: dict[str, Any]) -> list[str]:
    sensitive = ("authorization", "cookie", "key", "password", "secret", "token")
    return [
        "<sensitive>" if any(word in key.lower() for word in sensitive) else key
        for key in arguments
    ]


def _result_summary(result: Any) -> str:
    shape = getattr(result, "shape", None)
    if isinstance(shape, tuple) and len(shape) == 2:
        return f"{type(result).__name__}, {shape[0]} rows x {shape[1]} columns"
    if isinstance(result, str):
        return f"str, {len(result)} chars"
    if isinstance(result, bytes):
        return f"bytes, {len(result)} bytes"
    if isinstance(result, dict):
        return f"dict, {len(result)} keys"
    if isinstance(result, (list, tuple, set, frozenset)):
        return f"{type(result).__name__}, {len(result)} items"
    return type(result).__name__
