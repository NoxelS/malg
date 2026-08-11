"""The first, intentionally minimal, market-research agent."""

from __future__ import annotations

from nooa import Agent

from malg.utils.decorators import use_default_llm_endpoint


@use_default_llm_endpoint()
class MarketResearchAgent(Agent):
    """You are the market research agent for malg."""

    async def say_hello(self) -> str:
        """Reply with a short, friendly hello from MarketResearchAgent."""
        ...
