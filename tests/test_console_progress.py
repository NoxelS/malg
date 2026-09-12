"""Tests for safe, live NOOA console progress output."""

from __future__ import annotations

import asyncio
from io import StringIO
from types import SimpleNamespace

from nooa.events import LLMCallEnd, LLMCallStart

from malg.utils.console_progress import ConsoleProgress


class _EurostatAgent:
    async def get_data_frame(
        self, code: str, *, filter_pars: dict[str, str] | None = None, flags: bool = False
    ) -> object:
        return object()


class _DataFrame:
    shape = (27, 8)


def test_agent_result_metadata_and_timestamps_are_rendered_without_data() -> None:
    output = StringIO()
    clock_values = iter((10.0, 11.25))
    progress = ConsoleProgress(
        stream=output,
        clock=lambda: next(clock_values),
        timestamp=lambda: "12:34:56",
        color=False,
    )
    context = SimpleNamespace(
        agent=_EurostatAgent(),
        method_name="get_data_frame",
        args=("isoc_eb_ai",),
        kwargs={"filter_pars": {"geo": "DE", "time": "2025"}},
        result=None,
    )

    async def next_call(call: SimpleNamespace) -> SimpleNamespace:
        call.result = _DataFrame()
        return call

    asyncio.run(progress._agent_call(context, next_call))

    assert output.getvalue().splitlines() == [
        "[12:34:56] AGENT get_data_frame (code='isoc_eb_ai', filter dimensions=2) started",
        "[12:34:56] AGENT get_data_frame (code='isoc_eb_ai', filter dimensions=2) finished: "
        "_DataFrame, 27 rows x 8 columns in 1.2s",
    ]


def test_llm_lifecycle_is_timestamped_and_colored(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    output = StringIO()
    progress = ConsoleProgress(
        stream=output,
        clock=iter((2.0, 5.5)).__next__,
        timestamp=lambda: "01:02:03",
    )
    progress._llm_call_start(
        LLMCallStart(
            method_name="find_campaign",
            strategy="CodeActStrategy",
            generation_id="gen-1",
            turn_number=2,
        )
    )
    progress._llm_call_end(
        LLMCallEnd(
            method_name="find_campaign",
            strategy="CodeActStrategy",
            generation_id="gen-1",
            turn_number=2,
        )
    )

    assert output.getvalue().splitlines() == [
        "\033[2m[01:02:03]\033[0m \033[35mLLM  \033[0m find_campaign turn 2 started",
        "\033[2m[01:02:03]\033[0m \033[32mLLM  \033[0m find_campaign turn 2 finished in 3.5s",
    ]


def test_mcp_progress_never_renders_argument_or_result_values() -> None:
    output = StringIO()
    progress = ConsoleProgress(
        stream=output,
        clock=iter((3.0, 4.0)).__next__,
        timestamp=lambda: "01:02:03",
        color=False,
    )

    call_id = progress.mcp_started(
        "lightpanda",
        "navigate",
        {"url": "https://private.example/?token=hidden", "api_key": "hidden"},
    )
    progress.mcp_finished(call_id, "lightpanda", "navigate", "private page body")

    rendered = output.getvalue()
    assert "[01:02:03] MCP" in rendered
    assert "args: url, <sensitive>" in rendered
    assert "str, 17 chars" in rendered
    assert "private.example" not in rendered
    assert "hidden" not in rendered
    assert "private page body" not in rendered
