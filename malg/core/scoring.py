"""Deterministic scoring and ranking for researched markets."""

from __future__ import annotations

from malg.core.models.market import MarketResearchResult, ScoreDimension, ScoredMarket

SCORECARD_VERSION = "1.0"
SCORE_WEIGHTS: dict[ScoreDimension, float] = {
    ScoreDimension.DEMAND_INTENSITY: 1.2,
    ScoreDimension.SERVICE_FIT: 1.1,
    ScoreDimension.DIGITAL_READINESS: 1.0,
    ScoreDimension.ECONOMIC_CAPACITY: 0.8,
    ScoreDimension.FREELANCER_ACCESSIBILITY: 1.2,
    ScoreDimension.COMPETITIVE_WHITESPACE: 0.7,
    ScoreDimension.GEOGRAPHIC_LANGUAGE_FIT: 0.8,
    ScoreDimension.LEAD_DISCOVERABILITY: 1.0,
    ScoreDimension.TIME_TO_FIRST_ENGAGEMENT: 1.1,
    ScoreDimension.REGULATORY_DELIVERY_FEASIBILITY: 1.1,
}


def calculate_market_score(market: ScoredMarket) -> float:
    """Return a normalized 0-100 score from the fixed 1-5 scorecard."""
    weighted = sum(component.score * SCORE_WEIGHTS[component.dimension] for component in market.score_components)
    maximum = 5 * sum(SCORE_WEIGHTS.values())
    return round(100 * weighted / maximum, 1)


def rank_markets(result: MarketResearchResult) -> MarketResearchResult:
    """Overwrite model-supplied totals and ranks using stable deterministic rules."""
    scored = [market.model_copy(update={"total_score": calculate_market_score(market), "rank": None}) for market in result.markets]
    scored.sort(key=lambda market: (-(market.total_score or 0), market.market_id))
    ranked = [market.model_copy(update={"rank": rank}) for rank, market in enumerate(scored, 1)]
    return result.model_copy(update={"scorecard_version": SCORECARD_VERSION, "markets": ranked})
