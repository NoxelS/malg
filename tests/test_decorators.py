"""Tests for configuration forwarded to NOOA's LiteLLM client."""

from __future__ import annotations

from nooa import Agent

from malg.config import LLMConfig
from malg.utils import decorators


def test_default_llm_endpoint_sends_litellm_input_budget_header(
    monkeypatch,
) -> None:
    """Forward the configured prompt guardrail without exposing credentials."""
    config = LLMConfig(
        model="test-model",
        provider="openai",
        api_base="https://gateway.example/v1",
        api_key=None,
        context_window=262144,
        max_tokens=4096,
        headroom_compression=True,
        request_timeout_seconds=30,
    )
    monkeypatch.setattr(decorators, "_default_llm_config", config)

    @decorators.use_default_llm_endpoint()
    class ConfiguredAgent(Agent):
        """Minimal NOOA agent used to inspect constructed client settings."""

    assert ConfiguredAgent._agent_llm.context_window == 262144
    assert ConfiguredAgent._agent_llm.config["max_tokens"] == 4096
    assert ConfiguredAgent._agent_llm.config["extra_body"] == {
        "guardrails": ["headroom-compression"]
    }
