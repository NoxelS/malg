"""Direct OpenAI-SDK transport that satisfies NOOA's LLM contract.

The adapter deliberately speaks the Chat Completions protocol because MALG's
configured endpoints are OpenAI-compatible gateways and NOOA renders
chat-shaped conversation history.  It does not use LiteLLM for requests.
"""

from __future__ import annotations

import inspect
from typing import Any, cast

from nooa.unifiedllm import LLMResponse, Tool, ToolCall, UnifiedLLM
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel


class OpenAIChatClient(UnifiedLLM):
    """Adapt synchronous and asynchronous OpenAI SDK clients for NOOA.

    Args:
        model: Gateway-visible model identifier. It is sent unchanged, so a
            gateway alias such as ``nc-medium`` remains a bare alias.
        api_base: OpenAI-compatible Chat Completions base URL.
        api_key: Optional API key. When omitted, the OpenAI SDK may read its
            normal environment variable when the client is first used.
        context_window: Explicit input-token capacity used by NOOA's context
            budgeting. The adapter does not infer it from the gateway.
        max_tokens: Optional default completion-token cap.
        request_timeout_seconds: Per-request OpenAI SDK timeout.
        extra_body: Optional gateway-specific request fields.
        parallel_tool_calls: Whether a model may return a batch of function
            calls in one response. NOOA still executes such calls sequentially.
        sync_client: Optional injected SDK-compatible client for deterministic
            tests.
        async_client: Optional injected SDK-compatible client for deterministic
            tests.
    """

    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str | None,
        context_window: int | None,
        max_tokens: int | None,
        request_timeout_seconds: int,
        extra_body: dict[str, object] | None = None,
        parallel_tool_calls: bool = False,
        sync_client: Any | None = None,
        async_client: Any | None = None,
    ) -> None:
        request_defaults: dict[str, object] = {}
        if max_tokens is not None:
            request_defaults["max_tokens"] = max_tokens
        if extra_body:
            request_defaults["extra_body"] = extra_body
        super().__init__(model, **request_defaults)
        self.api_base = api_base
        self.api_key = api_key
        self._context_window = context_window
        self.request_timeout_seconds = request_timeout_seconds
        self.parallel_tool_calls = parallel_tool_calls
        self._sync_client = sync_client
        self._async_client = async_client

    @property
    def context_window(self) -> int | None:
        """Return the explicitly configured model input capacity."""
        return self._context_window

    def count_tokens(self, text: str) -> int:
        """Return a conservative local estimate when a caller requests one.

        NOOA's runtime calibrates context sizing from provider-reported usage,
        so this estimate is only a fallback and intentionally avoids LiteLLM's
        model registry/tokenizer path.
        """
        return max(1, (len(text) + 3) // 4)

    def _make_sync_client(self) -> OpenAI:
        return OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.request_timeout_seconds,
        )

    def _make_async_client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.request_timeout_seconds,
        )

    def _sync(self) -> Any:
        if self._sync_client is None:
            self._sync_client = self._make_sync_client()
        return self._sync_client

    def _async(self) -> Any:
        if self._async_client is None:
            self._async_client = self._make_async_client()
        return self._async_client

    def _request_params(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            **self.config,
            **{name: value for name, value in kwargs.items() if value is not None},
        }
        if tools:
            params["tools"] = [self._tool_schema(tool) for tool in tools]
            params["parallel_tool_calls"] = self.parallel_tool_calls
        return params

    @staticmethod
    def _tool_schema(tool: Tool) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.get_parameter_schema(),
            },
        }

    @staticmethod
    def _finish_reason(raw_reason: str | None) -> str:
        if raw_reason == "length":
            return "length"
        if raw_reason in {"content_filter", "error"}:
            return "error"
        if raw_reason == "tool_calls":
            return "tool_calls"
        return "stop"

    @staticmethod
    def _usage(raw_usage: Any) -> dict[str, Any] | None:
        if raw_usage is None:
            return None
        if hasattr(raw_usage, "model_dump"):
            return cast("dict[str, Any]", raw_usage.model_dump())
        if isinstance(raw_usage, dict):
            return dict(raw_usage)
        return {
            "prompt_tokens": getattr(raw_usage, "prompt_tokens", 0),
            "completion_tokens": getattr(raw_usage, "completion_tokens", 0),
            "total_tokens": getattr(raw_usage, "total_tokens", 0),
        }

    @staticmethod
    def _reasoning(message: Any) -> str | None:
        reasoning = getattr(message, "reasoning", None) or getattr(
            message, "reasoning_content", None
        )
        if reasoning is not None:
            return cast("str", reasoning)
        extra = getattr(message, "model_extra", None)
        if isinstance(extra, dict):
            return cast("str | None", extra.get("reasoning_content"))
        return None

    @staticmethod
    def _assistant_message(message: Any, raw_tool_calls: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "role": "assistant",
            "content": getattr(message, "content", None) or "",
        }
        if raw_tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name or "",
                        "arguments": call.function.arguments,
                    },
                }
                for call in raw_tool_calls
            ]
        reasoning_items = getattr(message, "reasoning_items", None)
        if reasoning_items:
            result["reasoning_items"] = [
                item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
                for item in reasoning_items
            ]
        return result

    def _response(self, raw_response: Any, output_model: type[BaseModel] | None) -> LLMResponse:
        if not getattr(raw_response, "choices", None):
            raise ValueError("OpenAI response did not include a completion choice.")
        choice = raw_response.choices[0]
        message = choice.message
        raw_tool_calls = list(getattr(message, "tool_calls", None) or [])
        reasoning = self._reasoning(message)
        usage = self._usage(getattr(raw_response, "usage", None))

        if raw_tool_calls:
            return LLMResponse(
                raw_response=raw_response,
                content=getattr(message, "content", None) or "",
                tool_calls=[
                    ToolCall(
                        id=call.id,
                        name=call.function.name or "",
                        arguments=call.function.arguments,
                    )
                    for call in raw_tool_calls
                ],
                finish_reason="tool_calls",
                assistant_message=self._assistant_message(message, raw_tool_calls),
                reasoning=reasoning,
                usage=usage,
            )

        content = getattr(message, "content", None) or ""
        if output_model is not None:
            parsed = getattr(message, "parsed", None)
            if parsed is None:
                refusal = getattr(message, "refusal", None)
                if refusal:
                    raise ValueError(f"Structured output was refused: {refusal}")
                parsed = output_model.model_validate_json(content)
            content = parsed

        return LLMResponse(
            raw_response=raw_response,
            content=content,
            tool_calls=[],
            finish_reason=cast("Any", self._finish_reason(getattr(choice, "finish_reason", None))),
            assistant_message=self._assistant_message(message, []),
            reasoning=reasoning,
            usage=usage,
        )

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Make one non-streaming synchronous Chat Completions request."""
        params = self._request_params(messages, tools, kwargs)
        completions = self._sync().chat.completions
        raw_response = (
            completions.parse(**params, response_format=output_model)
            if output_model is not None
            else completions.create(**params)
        )
        return self._response(raw_response, output_model)

    async def acall(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Make one non-streaming asynchronous Chat Completions request."""
        params = self._request_params(messages, tools, kwargs)
        completions = self._async().chat.completions
        raw_response = (
            await completions.parse(**params, response_format=output_model)
            if output_model is not None
            else await completions.create(**params)
        )
        return self._response(raw_response, output_model)

    def close(self) -> None:
        """Close an owned synchronous SDK client, if one was created."""
        close = getattr(self._sync_client, "close", None)
        if callable(close):
            close()
        self._sync_client = None

    async def aclose(self) -> None:
        """Close owned synchronous and asynchronous SDK clients."""
        self.close()
        close = getattr(self._async_client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        self._async_client = None
