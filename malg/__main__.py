"""Run the structured market research and ICP pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path

from malg.config import PipelineConfig, get_pipeline_config, load_settings
from malg.core.agents.icp_research import ICPResearchAgent
from malg.core.agents.market_research import MarketResearchAgent
from malg.core.browser_support import aclose_browser
from malg.core.models.market import MarketResearchResult
from malg.core.results import write_icp_result, write_market_result
from malg.core.scoring import rank_markets


async def run_pipeline(config: PipelineConfig) -> list[Path]:
    """Research markets, rank them, then research and persist one ICP per market."""
    market_agent = MarketResearchAgent()

    # First discover compact market hypotheses, then research each market separately.
    try:
        discovery = await market_agent.discover(config.market_count)
        if len(discovery.markets) > config.market_count:
            raise ValueError(f"Market discovery returned {len(discovery.markets)} markets; configured maximum is {config.market_count}.")

        researched_markets = []
        for seed in discovery.markets:
            researched = await market_agent.research_market(seed)
            if researched.market_id != seed.market_id:
                raise ValueError(f"Researched market_id {researched.market_id!r} does not match discovered market_id {seed.market_id!r}.")
            researched_markets.append(researched)

        market_result = rank_markets(
            MarketResearchResult(
                research_date=discovery.research_date,
                geographic_scope=discovery.geographic_scope,
                methodology_summary=discovery.methodology_summary,
                markets=researched_markets,
                limitations=discovery.limitations,
            )
        )
    finally:
        await aclose_browser(market_agent.browser)

    # Persist the market research results to disk, overwriting any existing files if configured to do so.
    written = [write_market_result(market_result, config.output_root, overwrite=config.overwrite)]

    # Limit the number of market segments to research based on the configuration.
    markets = market_result.markets[: config.top_n] if config.top_n is not None else market_result.markets

    # For each market segment, let the ICP research agent analyze the market and produce a recommended ideal customer profile (ICP).
    for market in markets:
        icp_agent = ICPResearchAgent()
        try:
            icp = await icp_agent.research(market)
        finally:
            await aclose_browser(icp_agent.browser)

        # Persist the ICP research results to disk, overwriting any existing files if configured to do so.
        if icp.market_id != market.market_id:
            print(f"ICP market_id {icp.market_id!r} does not match {market.market_id!r}.")
        else:
            written.extend(write_icp_result(icp, config.output_root, overwrite=config.overwrite))

    return written


async def main() -> None:
    paths = await run_pipeline(get_pipeline_config(load_settings()))

    print("Generated results:")
    for path in paths:
        print(path)


if __name__ == "__main__":
    asyncio.run(main())
