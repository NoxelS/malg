"""Run the bounded campaign-discovery smoke entry point."""

from __future__ import annotations

import asyncio

from malg.core.agents.campaign_research import CampaignResearchAgent
from malg.core.browser_support import aclose_browser
from malg.core.models.campaign import CampaignCandidate


async def find_one_campaign() -> CampaignCandidate:
    """Research one campaign and release its browser session without persisting it."""
    agent = CampaignResearchAgent()
    try:
        return await agent.find_campaign()
    finally:
        await aclose_browser(agent.browser)


async def main() -> None:
    campaign = await find_one_campaign()
    print(campaign.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
