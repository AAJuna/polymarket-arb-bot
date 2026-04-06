"""
Arbitrage Detector — finds mispricing opportunities across three strategies:
  A. Same-market mispricing  (YES + NO cost < $1 after fees)
  B. Cross-market arbitrage  (sibling markets for same event don't sum to 1)
  C. Odds comparison         (Polymarket price diverges from sportsbook consensus)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

import config
import data_feeds
from data_feeds import ExternalOdds
from logger_setup import get_logger
from scanner import MarketData
from utils import TTLCache, fee_adjusted_cost, retry, utcnow

logger = get_logger(__name__)

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

# Cache raw orderbooks for 5 seconds (avoid hammering CLOB)
_book_cache = TTLCache(ttl_seconds=5)


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------

@dataclass
class Opportunity:
    type: str                  # "same_market" | "cross_market" | "odds_comparison"
    market_id: str
    condition_id: str
    token_id: str
    side: str                  # "BUY" | "SELL"
    price: float               # target execution price
    edge_pct: float
    confidence_source: str
    yes_price: float
    no_price: float
    question: str
    end_date: datetime
    raw_data: MarketData
    external_odds: Optional[ExternalOdds] = None
    paired_token_id: Optional[str] = None   # for same_market: the other side
    created_at: datetime = field(default_factory=utcnow)

    def __str__(self):
        return (
            f"[{self.type}] {self.question[:60]} | "
            f"{self.side} @ {self.price:.3f} | edge={self.edge_pct:.1f}%"
        )


# ---------------------------------------------------------------------------
# Orderbook fetcher
# ---------------------------------------------------------------------------

@retry(max_attempts=2, base_delay=0.5, exceptions=(requests.RequestException,))
def _get_best_ask(token_id: str) -> Optional[float]:
    """Return the best ask price for a token from the CLOB orderbook."""
    cached = _book_cache.get(token_id)
    if cached is not None:
        return cached

    url = f"{config.POLYMARKET_HOST}/book"
    resp = _session.get(url, params={"token_id": token_id}, timeout=5)
    resp.raise_for_status()
    data = resp.json()

    asks = data.get("asks") or []
    if not asks:
        return None

    # asks are sorted ascending; first entry is best (lowest) ask
    best_ask = float(asks[0].get("price", 0))
    _book_cache.set(token_id, best_ask)
    return best_ask


# ---------------------------------------------------------------------------
# Strategy A — Same-market mispricing
# ---------------------------------------------------------------------------

def _find_same_market_opportunities(markets: list[MarketData]) -> list[Opportunity]:
    """
    Buy YES + NO when total fee-adjusted cost < $1.
    One guaranteed dollar of payout costs less than one dollar to acquire.
    """
    opps: list[Opportunity] = []

    for m in markets:
        # Pre-filter using Gamma API prices before paying CLOB API cost.
        # If Gamma prices already sum to >= 0.98, live orderbook won't show arb.
        gamma_sum = m.yes_price + m.no_price
        if gamma_sum >= 0.98:
            continue

        try:
            ask_yes = _get_best_ask(m.yes_token_id)
            ask_no = _get_best_ask(m.no_token_id)
        except Exception as e:
            logger.debug(f"Orderbook fetch failed for {m.market_id}: {e}")
            continue

        if ask_yes is None or ask_no is None:
            continue

        # Fee-adjusted costs
        cost_yes = fee_adjusted_cost(ask_yes, m.fee_rate_yes)
        cost_no = fee_adjusted_cost(ask_no, m.fee_rate_no)
        total_cost = cost_yes + cost_no

        if total_cost >= 1.0:
            continue

        edge_pct = (1.0 - total_cost) / total_cost * 100
        if edge_pct < config.MIN_EDGE_PCT:
            continue

        logger.debug(
            f"Same-market arb: {m.question[:50]} | "
            f"cost_yes={cost_yes:.4f} cost_no={cost_no:.4f} total={total_cost:.4f} edge={edge_pct:.2f}%"
        )

        # Create two linked opportunities (must execute both)
        opps.append(Opportunity(
            type="same_market",
            market_id=m.market_id,
            condition_id=m.condition_id,
            token_id=m.yes_token_id,
            side="BUY",
            price=ask_yes,
            edge_pct=edge_pct,
            confidence_source="mispricing",
            yes_price=m.yes_price,
            no_price=m.no_price,
            question=m.question,
            end_date=m.end_date,
            raw_data=m,
            paired_token_id=m.no_token_id,
        ))
        opps.append(Opportunity(
            type="same_market",
            market_id=m.market_id,
            condition_id=m.condition_id,
            token_id=m.no_token_id,
            side="BUY",
            price=ask_no,
            edge_pct=edge_pct,
            confidence_source="mispricing",
            yes_price=m.yes_price,
            no_price=m.no_price,
            question=m.question,
            end_date=m.end_date,
            raw_data=m,
            paired_token_id=m.yes_token_id,
        ))

    return opps


# ---------------------------------------------------------------------------
# Strategy B — Cross-market arbitrage
# ---------------------------------------------------------------------------

def _find_cross_market_opportunities(markets: list[MarketData]) -> list[Opportunity]:
    """
    Group markets by event. If probabilities of all outcomes don't sum to ~1,
    the cheapest outcome is underpriced.
    """
    opps: list[Opportunity] = []

    # Build event groups
    event_groups: dict[str, list[MarketData]] = defaultdict(list)
    for m in markets:
        if m.events:
            event_id = str(m.events[0].get("id", ""))
            if event_id:
                event_groups[event_id].append(m)

    for event_id, group in event_groups.items():
        if len(group) < 2:
            continue

        # Skip multi-outcome events (elections, award races) where we only see
        # a partial subset of candidates — their sum will always look low.
        # Real cross-market arb applies to binary/ternary events (2-3 outcomes).
        if len(group) > 4:
            continue

        # Skip if any market has YES price < 0.05 — long shots in big fields,
        # not genuine mispricing.
        if any(m.yes_price < 0.05 for m in group):
            continue

        total_yes_prob = sum(m.yes_price for m in group)

        # Sum should be ~1.0 for a complete binary/ternary event.
        # If sum < 0.60, we're almost certainly missing outcomes — skip.
        if total_yes_prob < 0.60 or total_yes_prob >= 0.95:
            continue

        gap = 1.0 - total_yes_prob
        edge_pct = gap / total_yes_prob * 100

        # Cap at 50% — anything higher is a data artifact, not real arb.
        if edge_pct < config.MIN_EDGE_PCT or edge_pct > 50.0:
            continue

        # BUY the cheapest YES token (most underpriced)
        cheapest = min(group, key=lambda m: m.yes_price)

        logger.debug(
            f"Cross-market arb: event={event_id} total_prob={total_yes_prob:.3f} "
            f"gap={gap:.3f} edge={edge_pct:.1f}%"
        )

        opps.append(Opportunity(
            type="cross_market",
            market_id=cheapest.market_id,
            condition_id=cheapest.condition_id,
            token_id=cheapest.yes_token_id,
            side="BUY",
            price=cheapest.yes_price,
            edge_pct=edge_pct,
            confidence_source="cross_market",
            yes_price=cheapest.yes_price,
            no_price=cheapest.no_price,
            question=cheapest.question,
            end_date=cheapest.end_date,
            raw_data=cheapest,
        ))

    return opps


# ---------------------------------------------------------------------------
# Strategy C — Odds comparison
# ---------------------------------------------------------------------------

def _find_odds_comparison_opportunities(markets: list[MarketData]) -> list[Opportunity]:
    """
    Compare Polymarket YES price with consensus sportsbook probability.
    If sportsbook thinks team wins 60% but Polymarket prices YES at 50¢ → 10% edge.
    """
    if not config.ODDS_API_KEY:
        return []

    opps: list[Opportunity] = []

    for m in markets:
        ext = data_feeds.get_odds_for_market(m.question, m.end_date)
        if ext is None:
            continue

        # Try to determine if the YES outcome corresponds to the home or away team
        q_lower = m.question.lower()
        home_norm = ext.home_team.lower()
        away_norm = ext.away_team.lower()

        # Simple heuristic: if home team appears first in question
        home_in_q = home_norm.split()[0] in q_lower if ext.home_team else False
        away_in_q = away_norm.split()[0] in q_lower if ext.away_team else False

        sportsbook_prob: Optional[float] = None
        if home_in_q and not away_in_q:
            sportsbook_prob = ext.home_prob
        elif away_in_q and not home_in_q:
            sportsbook_prob = ext.away_prob
        elif home_in_q and away_in_q:
            # Both teams mentioned — use the one closer to YES price
            home_diff = abs(ext.home_prob - m.yes_price)
            away_diff = abs(ext.away_prob - m.yes_price)
            sportsbook_prob = ext.home_prob if home_diff < away_diff else ext.away_prob
        else:
            continue

        if sportsbook_prob is None:
            continue

        edge_pct = (sportsbook_prob - m.yes_price) * 100

        if edge_pct < config.MIN_EDGE_PCT:
            continue

        logger.debug(
            f"Odds arb: {m.question[:50]} | "
            f"polymarket={m.yes_price:.3f} sportsbook={sportsbook_prob:.3f} edge={edge_pct:.1f}%"
        )

        opps.append(Opportunity(
            type="odds_comparison",
            market_id=m.market_id,
            condition_id=m.condition_id,
            token_id=m.yes_token_id,
            side="BUY",
            price=m.yes_price,
            edge_pct=edge_pct,
            confidence_source="odds_comparison",
            yes_price=m.yes_price,
            no_price=m.no_price,
            question=m.question,
            end_date=m.end_date,
            raw_data=m,
            external_odds=ext,
        ))

    return opps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_opportunities(markets: list[MarketData]) -> list[Opportunity]:
    """Run all three arbitrage strategies and return combined opportunities."""
    if not markets:
        return []

    a_opps = _find_same_market_opportunities(markets)
    b_opps = _find_cross_market_opportunities(markets)
    c_opps = _find_odds_comparison_opportunities(markets)

    all_opps = a_opps + b_opps + c_opps

    # Deduplicate by token_id + side (prefer highest edge)
    seen: dict[str, Opportunity] = {}
    for opp in all_opps:
        key = f"{opp.token_id}:{opp.side}"
        if key not in seen or opp.edge_pct > seen[key].edge_pct:
            seen[key] = opp

    result = sorted(seen.values(), key=lambda o: o.edge_pct, reverse=True)

    logger.info(
        f"Arbitrage scan: {len(a_opps)} same-market, "
        f"{len(b_opps)} cross-market, {len(c_opps)} odds-comparison "
        f"→ {len(result)} unique opportunities"
    )
    return result
