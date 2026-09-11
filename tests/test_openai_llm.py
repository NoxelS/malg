"""Observable behavior tests for MALG's direct OpenAI SDK adapter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from nooa.unifiedllm import Tool
from pydantic import BaseModel

from malg.core.openai_llm import OpenAIChatClient


class _Result(BaseModel):
    """Small structured result used to exercise SDK parsing."""

    label: str


class _AsyncCompletions:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.create_request: dict[str, Any] | None = None
        self.parse_request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.create_request = kwargs
        return self.response

    async def parse(self, **kwargs: Any) -> Any:
        self.parse_request = kwargs
        return self.response


class _AsyncClient:
    def __init__(self, response: Any) -> None:
        self.completions = _AsyncCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _SyncCompletions:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.create_request: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        self.create_request = kwargs
        return self.response


class _SyncClient:
    def __init__(self, response: Any) -> None:
        self.completions = _SyncCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _response(message: Any, finish_reason: str = "stop") -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )


def _client(*, async_client: Any | None = None, sync_client: Any | None = None) -> OpenAIChatClient:
    return OpenAIChatClient(
        model="nc-medium",
        api_base="https://gateway.example/v1",
        api_key=None,
        context_window=262144,
        max_tokens=4096,
        request_timeout_seconds=30,
        extra_body={"guardrails": ["headroom-compression"]},
        parallel_tool_calls=True,
        async_client=async_client,
        sync_client=sync_client,
    )


def test_acall_forwards_gateway_options_and_normalizes_tool_calls() -> None:
    def execute_python(code: str) -> str:
        """Execute one generated Python cell."""
        return code

    tool_call = SimpleNamespace(
        id="call_123",
        function=SimpleNamespace(name="execute_python", arguments='{"code":"x = 1"}'),
    )
    async_client = _AsyncClient(
        _response(
            SimpleNamespace(content="", tool_calls=[tool_call], reasoning_content="thinking"),
            "tool_calls",
        )
    )

    response = asyncio.run(
        _client(async_client=async_client).acall(
            [{"role": "user", "content": "run code"}],
            tools=[Tool("execute_python", "Run Python.", execute_python)],
            prompt_cache_key="agent-codeact",
        )
    )

    assert response.finish_reason == "tool_calls"
    assert response.reasoning == "thinking"
    assert response.tool_calls[0].name == "execute_python"
    assert response.assistant_message["tool_calls"][0]["id"] == "call_123"
    request = async_client.completions.create_request
    assert request is not None
    assert request["model"] == "nc-medium"
    assert request["max_tokens"] == 4096
    assert request["extra_body"] == {"guardrails": ["headroom-compression"]}
    assert request["parallel_tool_calls"] is True
    assert request["prompt_cache_key"] == "agent-codeact"
    assert request["tools"][0]["function"]["name"] == "execute_python"


def test_acall_returns_sdk_parsed_pydantic_output() -> None:
    async_client = _AsyncClient(
        _response(
            SimpleNamespace(
                content='{"label":"ready"}', tool_calls=[], parsed=_Result(label="ready")
            )
        )
    )

    response = asyncio.run(
        _client(async_client=async_client).acall(
            [{"role": "user", "content": "label this"}], output_model=_Result
        )
    )

    assert response.content == _Result(label="ready")
    assert response.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    request = async_client.completions.parse_request
    assert request is not None
    assert request["response_format"] is _Result


def test_call_uses_sync_client_and_closes_owned_clients() -> None:
    sync_client = _SyncClient(_response(SimpleNamespace(content="done", tool_calls=[])))
    async_client = _AsyncClient(_response(SimpleNamespace(content="unused", tool_calls=[])))
    client = _client(sync_client=sync_client, async_client=async_client)

    response = client.call([{"role": "user", "content": "finish"}])
    asyncio.run(client.aclose())

    assert response.content == "done"
    assert sync_client.completions.create_request is not None
    assert sync_client.closed is True
    assert async_client.closed is True
