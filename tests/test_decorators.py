"""Tests for configuration forwarded to MALG's direct OpenAI client."""

from __future__ import annotations

from nooa import Agent

from malg.config import LLMConfig
from malg.core.openai_llm import OpenAIChatClient
from malg.utils import decorators


def test_default_llm_endpoint_forwards_gateway_request_options(
    monkeypatch,
) -> None:
    """Forward the configured prompt guardrail without exposing credentials."""
    config = LLMConfig(
        model="test-model",
        api_base="https://gateway.example/v1",
        api_key=None,
        context_window=262144,
        max_tokens=4096,
        headroom_compression=True,
        enable_thinking=False,
        parallel_tool_calls=True,
        request_timeout_seconds=30,
    )
    monkeypatch.setattr(decorators, "_default_llm_config", config)

    @decorators.use_default_llm_endpoint()
    class ConfiguredAgent(Agent):
        """Minimal NOOA agent used to inspect constructed client settings."""

    assert isinstance(ConfiguredAgent._agent_llm, OpenAIChatClient)
    assert ConfiguredAgent._agent_llm.model == "test-model"
    assert ConfiguredAgent._agent_llm.context_window == 262144
    assert ConfiguredAgent._agent_llm.config["max_tokens"] == 4096
    assert ConfiguredAgent._agent_llm.config["extra_body"] == {
        "guardrails": ["headroom-compression"],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert ConfiguredAgent._agent_llm.parallel_tool_calls is True


def test_default_llm_endpoint_omits_provider_specific_body_when_not_configured(
    monkeypatch,
) -> None:
    """Keep ordinary OpenAI-compatible requests free of opt-in provider fields."""
    config = LLMConfig(
        model="test-model",
        api_base="https://api.openai.com/v1",
        api_key=None,
        context_window=None,
        max_tokens=None,
        headroom_compression=False,
        enable_thinking=None,
        parallel_tool_calls=False,
        request_timeout_seconds=30,
    )
    monkeypatch.setattr(decorators, "_default_llm_config", config)

    @decorators.use_default_llm_endpoint()
    class ConfiguredAgent(Agent):
        """Minimal NOOA agent used to inspect constructed client settings."""

    assert "extra_body" not in ConfiguredAgent._agent_llm.config
