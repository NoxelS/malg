"""Decorators for configuring NOOA agents."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from nooa import Agent  # type: ignore[attr-defined]  # NOOA re-exports Agent dynamically.

from malg.config import get_llm_config, load_settings
from malg.core.openai_llm import OpenAIChatClient

AgentType = TypeVar("AgentType", bound=Agent)

_default_llm_config = get_llm_config(load_settings())


def use_default_llm_endpoint(
    *, model: str | None = None
) -> Callable[[type[AgentType]], type[AgentType]]:
    """Configure a NOOA agent with a direct OpenAI-compatible client.

    The model is sent unchanged, allowing configured gateway aliases such as
    ``nc-medium``. Requests remain non-streaming and use the configured timeout.
    """
    config = _default_llm_config
    extra_body: dict[str, object] = {}
    if config.headroom_compression:
        # The gateway's optional pre-call compression extension remains valid
        # when requests are made through the OpenAI SDK.
        extra_body["guardrails"] = ["headroom-compression"]
    if config.enable_thinking is not None:
        extra_body["chat_template_kwargs"] = {"enable_thinking": config.enable_thinking}
    llm = OpenAIChatClient(
        model=model or config.model,
        api_base=config.api_base,
        api_key=config.api_key,
        context_window=config.context_window,
        max_tokens=config.max_tokens,
        request_timeout_seconds=config.request_timeout_seconds,
        extra_body=extra_body or None,
        parallel_tool_calls=config.parallel_tool_calls,
    )

    def configure_agent(agent_class: type[AgentType]) -> type[AgentType]:
        if not issubclass(agent_class, Agent):
            raise TypeError("use_default_llm_endpoint can only decorate NOOA Agent subclasses.")
        agent_class._agent_llm = llm
        return agent_class

    return configure_agent
