"""Run the minimal NOOA smoke agent."""

from __future__ import annotations

import asyncio

from malg.core.agents.market_research import MarketResearchAgent


async def main() -> None:
    agent = MarketResearchAgent()
    print(await agent.say_hello())


if __name__ == "__main__":
    asyncio.run(main())
