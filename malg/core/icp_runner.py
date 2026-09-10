"""Host-owned orchestration for bounded, non-overlapping ICP batches."""

from __future__ import annotations

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
) -> ICPBatchResult:
    """Research and durably claim the next configured distinct ICPs for a campaign.

    The runner is the authority for candidate counts, exclusions, uniqueness,
    persistence, and retry limits.  It returns a partial result only after the
    finite candidate space or configured attempt budget is exhausted.
    """
    effective_config = config or get_icp_config(load_settings())
    ledger = history or ICPHistoryLedger()
    owns_history = history is None
    researcher = agent or ICPResearchAgent()
    owns_agent = agent is None
    run_id = uuid4().hex
    accepted: list[ICPResult] = []
    rejections: list[ICPRejection] = []
    attempt = 0
    exhaustion_reason: str | None = None

    try:
        if owns_agent and hasattr(researcher, "event_manager"):
            ConsoleProgress().attach(researcher)

        for _slot in range(effective_config.batch_size):
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
                break

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
        if owns_agent and hasattr(researcher, "browser"):
            await aclose_browser(researcher.browser)
        if owns_history:
            ledger.close()
