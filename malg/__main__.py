"""Run the saved-campaign ICP batch smoke entry point."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from malg.core.agents.campaign_research import CampaignResearchAgent
from malg.core.browser_support import aclose_browser
from malg.core.icp_runner import research_icps
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPBatchResult, ICPResult
from malg.core.persistent_memory_support import close_persistent_memory
from malg.database.artifacts import persist_icps
from malg.database.session import make_engine, make_session_factory
from malg.utils.console_progress import ConsoleProgress

CAMPAIGN_PATH = Path("results/campaigns/1.json")


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


def load_saved_campaign(path: Path = CAMPAIGN_PATH) -> CampaignCandidate:
    """Load and validate the explicit campaign artifact used by the ICP smoke run."""
    return CampaignCandidate.model_validate(json.loads(path.read_text(encoding="utf-8")))


async def find_ten_icps(
    campaign: CampaignCandidate | None = None,
    *,
    on_accepted: Callable[[ICPResult], None] | None = None,
) -> ICPBatchResult:
    """Research the next configured ICP batch for a saved or supplied campaign."""
    return await research_icps(campaign or load_saved_campaign(), on_accepted=on_accepted)


async def main() -> None:
    """Run ICP research and store accepted results in the configured database."""
    campaign = load_saved_campaign()
    engine = make_engine()
    sessions = make_session_factory(engine)
    try:
        batch = await find_ten_icps(
            campaign,
            on_accepted=lambda icp: persist_icps(campaign, [icp], sessions),
        )
    finally:
        engine.dispose()
    print(batch.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
