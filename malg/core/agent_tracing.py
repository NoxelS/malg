"""Best-effort durable recording at worker, NOOA, and MCP boundaries."""

from __future__ import annotations

import contextvars
import logging
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from nooa.runtime.middleware import MIDDLEWARE_LLM_CALL, LLMCallContext

from malg.database import traces

_active_recorder: contextvars.ContextVar[AgentTraceRecorder | None] = contextvars.ContextVar(
    "malg_trace_recorder", default=None
)


def normalize(value: Any) -> Any:
    """Normalize arbitrary values for JSON storage."""
    return traces.normalize_json(value)


def record_active_event(event_type: str, payload: Any) -> None:
    """Append an event to the recorder currently bound to this task."""
    recorder = _active_recorder.get()
    if recorder is not None:
        recorder.event(event_type, payload)


class AgentTraceRecorder:
    """Persist one execution scope without making telemetry load-bearing."""

    def __init__(self, session_factory: Any, run_id: str) -> None:
        self.session_factory = session_factory
        self.run_id = run_id
        self.job_id: str | None = None
        self._pending: deque[tuple[str, int, str, str]] = deque()
        self._turn_ids: dict[tuple[str, int], int] = {}
        self._unsubscribe: list[Callable[[], None]] = []
        self._token: Any = None

    @classmethod
    def start_run(
        cls,
        session_factory: Any,
        job_id: str,
        scope: str,
        subject: Any,
        method_name: str,
        worker_token: str | None = None,
    ) -> AgentTraceRecorder:
        """Create a running scope before its work begins."""
        run_id = str(__import__("uuid").uuid4())
        with session_factory.begin() as session:
            traces.create_run(
                session,
                job_id,
                scope,
                type(subject).__name__ if not isinstance(subject, str) else subject,
                method_name,
                worker_token,
                run_id,
            )
        recorder = cls(session_factory, run_id)
        recorder.job_id = job_id
        return recorder

    def bind(self) -> Callable[[], None]:
        """Bind this recorder to logging and return an idempotent reset callback."""
        token = _active_recorder.set(self)
        self._token = token

        def reset() -> None:
            if self._token is token:
                _active_recorder.reset(token)
                self._token = None

        return reset

    def event(self, event_type: str, payload: Any, turn_id: int | None = None) -> None:
        """Append an event, swallowing persistence failures."""
        try:
            with self.session_factory.begin() as session:
                traces.append_event(session, self.run_id, event_type, payload, turn_id)
        except Exception:
            logging.getLogger(__name__).exception("agent trace persistence failed")

    def attach(self, agent: Any) -> None:
        """Subscribe to NOOA events and its exact LLM middleware boundary."""

        def on_event(event: Any) -> None:
            name = event.__class__.__name__
            payload = normalize(event)
            if name == "LLMCallStart":
                self._pending.append(
                    (event.generation_id, event.turn_number, event.method_name, event.strategy)
                )
            turn_id = self._turn_ids.get(
                (getattr(event, "generation_id", ""), getattr(event, "turn_number", 0))
            )
            self.event(name, payload, turn_id)
            if name == "LLMCallEnd":
                self._turn_ids.pop((event.generation_id, event.turn_number), None)

        self._unsubscribe.append(agent.event_manager.on("*", on_event))

        async def middleware(ctx: LLMCallContext, nxt: Any) -> LLMCallContext:
            identity = (
                self._pending.popleft()
                if self._pending
                else ("unknown", len(self._turn_ids) + 1, "unknown", "unknown")
            )
            generation_id, turn_number, method_name, strategy = identity
            turn_id: int | None = None
            try:
                with self.session_factory.begin() as session:
                    turn = traces.create_turn(
                        session,
                        self.run_id,
                        generation_id,
                        turn_number,
                        method_name,
                        strategy,
                        normalize(ctx.messages),
                        normalize(ctx.params),
                    )
                    turn_id = turn.turn_id
                self._turn_ids[(generation_id, turn_number)] = turn_id
            except Exception:
                logging.getLogger(__name__).exception("agent turn trace persistence failed")

            try:
                result = await nxt(ctx)
            except Exception as error:
                if turn_id is not None:
                    try:
                        with self.session_factory.begin() as session:
                            traces.finalize_turn(session, turn_id, success=False, error=error)
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "agent turn trace finalization failed"
                        )
                raise

            if turn_id is not None:
                try:
                    with self.session_factory.begin() as session:
                        traces.finalize_turn(session, turn_id, normalize(result.response), True)
                except Exception:
                    logging.getLogger(__name__).exception("agent turn trace finalization failed")
            return result

        self._unsubscribe.append(agent.event_manager.intercept(MIDDLEWARE_LLM_CALL, middleware))

    def finish_success(self, result: Any = None) -> None:
        """Finalize this scope successfully."""
        self._finish(True)

    def finish_failure(self, error: BaseException) -> None:
        """Finalize this scope with complete diagnostics."""
        self._finish(False, error)

    def _finish(self, success: bool, error: BaseException | None = None) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        try:
            with self.session_factory.begin() as session:
                traces.finalize_run(session, self.run_id, success, error)
        except Exception:
            logging.getLogger(__name__).exception("agent trace finalization failed")

    def close(self) -> None:
        """Release subscriptions and reset active context."""
        if self._token is not None:
            _active_recorder.reset(self._token)
            self._token = None


class _TraceLogHandler(logging.Handler):
    """Root handler that records application logs best-effort."""

    def emit(self, record: logging.LogRecord) -> None:
        recorder = _active_recorder.get()
        if recorder is None or record.name.startswith((__name__, "sqlalchemy")):
            return
        from contextlib import suppress

        with suppress(Exception):
            recorder.event(
                "log",
                {
                    "exception": logging.Formatter().formatException(record.exc_info)
                    if record.exc_info
                    else None,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )


def install_trace_log_handler() -> None:
    """Install the singleton root trace handler."""
    root = logging.getLogger()
    if not any(isinstance(handler, _TraceLogHandler) for handler in root.handlers):
        root.addHandler(_TraceLogHandler())
