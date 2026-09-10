"""Run the bounded campaign-discovery smoke entry point."""

from __future__ import annotations

import asyncio

from malg.core.agents.campaign_research import CampaignResearchAgent
from malg.core.browser_support import aclose_browser
from malg.core.models.campaign import CampaignCandidate
from malg.core.persistent_memory_support import close_persistent_memory
from malg.utils.console_progress import ConsoleProgress


async def find_one_campaign() -> CampaignCandidate:
    """Research one Eurostat-backed campaign with scoped persistent memory."""
    agent = CampaignResearchAgent()
    try:
        if hasattr(agent, "event_manager"):
            ConsoleProgress().attach(agent)
        return await agent.find_campaign()
    finally:
        if hasattr(agent, "browser"):
            await aclose_browser(agent.browser)
        close_persistent_memory(agent)


async def main() -> None:
    campaign = await find_one_campaign()
    print(campaign.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
