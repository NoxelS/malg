"""Run the minimal NOOA smoke agent."""

from __future__ import annotations

import asyncio

from malg.core.agents.market_research import MarketResearchAgent
from malg.core.browser_support import aclose_browser


async def main() -> None:
    agent = MarketResearchAgent()
    try:
        result = await agent.research_mcp()
        print(f"MarketResearchAgent says: {result}")
    finally:
        await aclose_browser(agent.browser)


if __name__ == "__main__":
    asyncio.run(main())
