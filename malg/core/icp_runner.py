"""Host-owned orchestration for bounded, non-overlapping ICP batches."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from malg.config import ICPConfig, get_icp_config, load_settings
from malg.core.agents.icp_research import ICPResearchAgent
from malg.core.browser_support import aclose_browser
from malg.core.icp_history import ICPHistoryLedger
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPBatchResult, ICPIdentity, ICPRejection, ICPResult
from malg.core.results import write_icp_batch_result
from malg.utils.console_progress import ConsoleProgress


class ICPResearcher(Protocol):
    """The narrow generated-agent boundary required by the deterministic runner."""

    async def research_one(
        self, campaign: CampaignCandidate, excluded_segments: list[ICPIdentity]
    ) -> ICPResult:
        """Return one candidate profile for the campaign."""


async def research_icps(
    campaign: CampaignCandidate,
    *,
    config: ICPConfig | None = None,
    history: ICPHistoryLedger | None = None,
    agent: ICPResearcher | None = None,
    on_accepted: Callable[[ICPResult], None] | None = None,
) -> ICPBatchResult:
    """Research and durably claim the next configured distinct ICPs for a campaign.

    The runner is the authority for candidate counts, exclusions, uniqueness,
    persistence, concurrency, and retry limits. At most ``config.concurrency``
    isolated generated-agent calls run at once. ``on_accepted`` is called
    synchronously after each accepted claim so host-owned storage can make every
    accepted ICP durable before its worker starts another research call. It
    returns a partial result only after the finite candidate space or configured
    attempt budget is exhausted.
    """
    effective_config = config or get_icp_config(load_settings())
    ledger = history or ICPHistoryLedger()
    owns_history = history is None
    researchers = (
        [agent]
        if agent is not None
        else [
            ICPResearchAgent()
            for _ in range(min(effective_config.batch_size, effective_config.concurrency))
        ]
    )
    owns_agent = agent is None
    run_id = uuid4().hex
    accepted: list[ICPResult] = []
    rejections: list[ICPRejection] = []
    attempt = 0
    exhaustion_reason: str | None = None

    try:
        if owns_agent:
            for researcher in researchers:
                if hasattr(researcher, "event_manager"):
                    ConsoleProgress().attach(researcher)

        async def research_slot(researcher: ICPResearcher) -> None:
            nonlocal attempt, exhaustion_reason
            claimed = False
            for _retry in range(effective_config.max_attempts_per_slot):
                attempt += 1
                exclusions = ledger.accepted_identities(
                    campaign.campaign_id, limit=effective_config.max_exclusion_cards
                )
                candidate = await researcher.research_one(campaign, exclusions)
                if candidate.campaign_id != campaign.campaign_id:
                    rejections.append(
                        ICPRejection(
                            attempt=attempt,
                            reason="candidate campaign_id did not match the requested campaign",
                            icp_id=candidate.icp_id,
                            segment_key=candidate.identity.segment_key(),
                        )
                    )
                    continue
                if ledger.claim(candidate, run_id=run_id):
                    try:
                        if on_accepted is not None:
                            on_accepted(candidate)
                    except Exception:
                        ledger.release(candidate, run_id=run_id)
                        raise
                    accepted.append(candidate)
                    claimed = True
                    break
                rejections.append(
                    ICPRejection(
                        attempt=attempt,
                        reason="candidate identity or icp_id already exists for this campaign",
                        icp_id=candidate.icp_id,
                        segment_key=candidate.identity.segment_key(),
                    )
                )
            if not claimed:
                exhaustion_reason = (
                    "No distinct ICP was produced within the configured attempt budget for a slot."
                )

        active_tasks: set[asyncio.Task[None]] = set()
        slot_count = effective_config.batch_size
        next_slot = 0

        while (next_slot < slot_count and exhaustion_reason is None) or active_tasks:
            while (
                next_slot < slot_count
                and exhaustion_reason is None
                and len(active_tasks) < len(researchers)
            ):
                researcher = researchers[next_slot % len(researchers)]
                active_tasks.add(asyncio.create_task(research_slot(researcher)))
                next_slot += 1

            done, active_tasks = await asyncio.wait(
                active_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                exception = task.exception()
                if exception is not None:
                    for active_task in active_tasks:
                        active_task.cancel()
                    await asyncio.gather(*active_tasks, return_exceptions=True)
                    raise exception

        result = ICPBatchResult(
            campaign_id=campaign.campaign_id,
            run_id=run_id,
            requested_count=effective_config.batch_size,
            icps=accepted,
            rejections=rejections,
            exhaustion_reason=exhaustion_reason,
        )
        write_icp_batch_result(result, effective_config.output_root)
        return result
    finally:
        if owns_agent:
            for researcher in researchers:
                if hasattr(researcher, "browser"):
                    await aclose_browser(researcher.browser)
        if owns_history:
            ledger.close()
