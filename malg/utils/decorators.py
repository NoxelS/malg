"""Decorators for configuring NOOA agents."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from nooa import Agent  # type: ignore[attr-defined]  # NOOA re-exports Agent dynamically.
from nooa.unifiedllm import get_llm_client

from malg.config import get_llm_config, load_settings

AgentType = TypeVar("AgentType", bound=Agent)

_default_llm_config = get_llm_config(load_settings())


def use_default_llm_endpoint(
    *, model: str = _default_llm_config.model
) -> Callable[[type[AgentType]], type[AgentType]]:
    """Configure a NOOA agent with the default endpoint and an optional model override."""
    resolved_model = model if "/" in model else f"{_default_llm_config.provider}/{model}"
    llm_options: dict[str, object] = {
        "custom_llm_provider": _default_llm_config.provider,
        "api_base": _default_llm_config.api_base,
        "api_key": _default_llm_config.api_key,
    }
    if _default_llm_config.context_window is not None:
        llm_options["context_window"] = _default_llm_config.context_window
    if _default_llm_config.max_tokens is not None:
        llm_options["max_tokens"] = _default_llm_config.max_tokens

    llm = get_llm_client(
        resolved_model,
        **llm_options,  # type: ignore[arg-type]  # NOOA forwards provider-specific options.
    )

    def configure_agent(agent_class: type[AgentType]) -> type[AgentType]:
        if not issubclass(agent_class, Agent):
            raise TypeError("use_default_llm_endpoint can only decorate NOOA Agent subclasses.")
        agent_class._agent_llm = llm
        return agent_class

    return configure_agent
