"""Run the bounded campaign-discovery smoke entry point."""

from __future__ import annotations

import asyncio

from malg.core.agents.campaign_research import CampaignResearchAgent
from malg.core.models.campaign import CampaignCandidate


async def find_one_campaign() -> CampaignCandidate:
    """Research one Eurostat-backed campaign without persisting it."""
    agent = CampaignResearchAgent()
    return await agent.find_campaign()


async def main() -> None:
    campaign = await find_one_campaign()
    print(campaign.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
