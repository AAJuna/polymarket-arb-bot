"""
Arbitrage Detector — finds mispricing opportunities across three strategies:
  A. Same-market mispricing  (YES + NO cost < $1 after fees)
  B. Cross-market arbitrage  (sibling markets for same event don't sum to 1)
  C. Odds comparison         (Polymarket price diverges from sportsbook consensus)
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

import config
import data_feeds
from data_feeds import ExternalOdds
from logger_setup import get_logger
from realtime_feed import get_shared_feed
from scanner import MarketData
from utils import TTLCache, compute_orderbook_depth, fee_adjusted_cost, retry, utcnow

logger = get_logger(__name__)

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

# Cache prices for 5 seconds
_price_cache = TTLCache(ttl_seconds=5)
_book_cache = TTLCache(ttl_seconds=5)

BATCH_SIZE = 100  # max token_ids per batch request

# Tracks wall-clock time when each token's price was last fetched (for freshness checks)
_price_timestamps: dict[str, float] = {}
# Stores full ask-level lists from /book responses (keyed by token_id)
_book_asks_cache: dict[str, list] = {}


# ---------------------------------------------------------------------------
# Batch price fetcher
# ---------------------------------------------------------------------------

@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _fetch_midpoints_batch(token_ids: list[str]) -> dict[str, float]:
    """Fetch midpoint prices for multiple tokens in one request.
    Returns dict of token_id → midpoint price.
    """
    results: dict[str, float] = {}
    # Split into chunks to avoid URL length limits
    for i in range(0, len(token_ids), BATCH_SIZE):
        chunk = token_ids[i:i + BATCH_SIZE]
        # Check cache first
        uncached = [t for t in chunk if _price_cache.get(t) is None]
        for t in chunk:
            cached = _price_cache.get(t)
            if cached is not None:
                results[t] = cached

        if not uncached:
            continue

        try:
            resp = _session.post(
                f"{config.POLYMARKET_HOST}/midpoints",
                json=[{"token_id": token_id} for token_id in uncached],
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            now = time.monotonic()
            for token_id, price_str in data.items():
                price = float(price_str)
                results[token_id] = price
                _price_cache.set(token_id, price)
                _price_timestamps[token_id] = now
        except Exception as e:
            logger.debug(f"Batch midpoint POST failed for chunk: {e}")
            try:
                resp = _session.get(
                    f"{config.POLYMARKET_HOST}/midpoints",
                    params={"token_ids": ",".join(uncached)},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                now = time.monotonic()
                for token_id, price_str in data.items():
                    price = float(price_str)
                    results[token_id] = price
                    _price_cache.set(token_id, price)
                    _price_timestamps[token_id] = now
            except Exception as fallback_error:
                logger.debug(f"Batch midpoint fallback GET failed for chunk: {fallback_error}")

    return results


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------

@dataclass
class Opportunity:
    type: str                  # "same_market" | "cross_market" | "odds_comparison"
    market_id: str
    condition_id: str
    token_id: str
    side: str                  # "YES" | "NO"
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
    value_score: float = 0.0                # ROI-weighted edge for ranking
    created_at: datetime = field(default_factory=utcnow)
    slug: str = ""
    event_slug: str = ""
    market_slug: str = ""
    market_url: str = ""

    def __str__(self):
        return (
            f"[{self.type}] {self.question[:60]} | "
            f"{self.side} @ {self.price:.3f} | edge={self.edge_pct:.1f}%"
        )


# ---------------------------------------------------------------------------
# Orderbook fetcher
# ---------------------------------------------------------------------------

def _apply_realtime_quote(token_id: str, include_asks: bool = True) -> Optional[float]:
    """Populate local caches from the shared realtime feed when possible."""
    feed = get_shared_feed()
    best_ask = feed.get_best_ask(token_id)
    if best_ask is None:
        return None

    _book_cache.set(token_id, best_ask)
    _price_timestamps[token_id] = max(
        _price_timestamps.get(token_id, 0.0),
        feed.get_quote_updated_monotonic(token_id),
    )

    if include_asks:
        asks = feed.get_orderbook_asks(token_id)
        if asks:
            _book_asks_cache[token_id] = asks

    return best_ask


def _get_cached_best_ask(token_id: str) -> Optional[float]:
    """Return best ask from realtime/local cache only, without HTTP fallback."""
    realtime_best_ask = _apply_realtime_quote(token_id, include_asks=False)
    if realtime_best_ask is not None:
        return realtime_best_ask
    return _book_cache.get(token_id)


@retry(max_attempts=2, base_delay=0.5, exceptions=(requests.RequestException,))
def _get_best_ask(token_id: str) -> Optional[float]:
    """Return the best ask price for a token from the CLOB orderbook."""
    realtime_best_ask = _apply_realtime_quote(token_id)
    if realtime_best_ask is not None:
        return realtime_best_ask

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
    _book_asks_cache[token_id] = asks          # store full depth for liquidity checks
    _price_timestamps[token_id] = time.monotonic()
    return best_ask


def _get_orderbook_asks(token_id: str) -> list:
    """Return the cached full asks list for a token (populated by _get_best_ask)."""
    realtime_asks = get_shared_feed().get_orderbook_asks(token_id)
    if realtime_asks:
        _book_asks_cache[token_id] = realtime_asks
        _price_timestamps[token_id] = max(
            _price_timestamps.get(token_id, 0.0),
            get_shared_feed().get_quote_updated_monotonic(token_id),
        )
        return realtime_asks
    return _book_asks_cache.get(token_id, [])


def _is_price_fresh(token_id: str) -> bool:
    """Return True if the token's price was fetched within MAX_STALE_MS milliseconds."""
    ts = _price_timestamps.get(token_id, 0.0)
    return (time.monotonic() - ts) * 1000 <= config.MAX_STALE_MS


# ---------------------------------------------------------------------------
# Strategy A — Same-market mispricing
# ---------------------------------------------------------------------------

def _find_same_market_opportunities(markets: list[MarketData]) -> list[Opportunity]:
    """
    Buy YES + NO when total fee-adjusted cost < $1.
    Uses executable ask-side orderbook prices rather than midpoint prices.
    """
    opps: list[Opportunity] = []

    # Pre-filter: only markets where Gamma prices suggest possible mispricing
    candidates = [m for m in markets if (m.yes_price + m.no_price) < 0.98]
    if not candidates:
        return []

    for m in candidates:
        ask_yes = _get_best_ask(m.yes_token_id)
        ask_no = _get_best_ask(m.no_token_id)

        if ask_yes is None or ask_no is None:
            continue

        # Reject stale prices — price must have been fetched within MAX_STALE_MS
        if not _is_price_fresh(m.yes_token_id) or not _is_price_fresh(m.no_token_id):
            logger.debug(f"Stale price skipped: {m.market_id}")
            continue

        asks_yes = _get_orderbook_asks(m.yes_token_id)
        asks_no  = _get_orderbook_asks(m.no_token_id)
        avg_yes, depth_yes = compute_orderbook_depth(asks_yes, config.TRADE_SIZE_TARGET_USD)
        avg_no, depth_no  = compute_orderbook_depth(asks_no,  config.TRADE_SIZE_TARGET_USD)
        if depth_yes < config.MIN_LIQUIDITY_DEPTH_USD or depth_no < config.MIN_LIQUIDITY_DEPTH_USD:
            logger.debug(
                f"Insufficient depth: {m.market_id} "
                f"YES=${depth_yes:.0f} NO=${depth_no:.0f} (min=${config.MIN_LIQUIDITY_DEPTH_USD:.0f})"
            )
            continue

        # Fee-adjusted costs from executable depth, not midpoint.
        cost_yes = fee_adjusted_cost(avg_yes, m.fee_rate_yes)
        cost_no = fee_adjusted_cost(avg_no, m.fee_rate_no)
        total_cost = cost_yes + cost_no

        # Use conservative threshold (< 0.985) instead of bare < 1.0
        if total_cost >= config.SAME_MARKET_COST_THRESHOLD:
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
            side="YES",
            price=ask_yes,
            edge_pct=edge_pct,
            confidence_source="mispricing",
            yes_price=m.yes_price,
            no_price=m.no_price,
            question=m.question,
            end_date=m.end_date,
            raw_data=m,
            paired_token_id=m.no_token_id,
            slug=m.slug,
            event_slug=m.event_slug,
            market_slug=m.market_slug,
            market_url=m.market_url,
        ))
        opps.append(Opportunity(
            type="same_market",
            market_id=m.market_id,
            condition_id=m.condition_id,
            token_id=m.no_token_id,
            side="NO",
            price=ask_no,
            edge_pct=edge_pct,
            confidence_source="mispricing",
            yes_price=m.yes_price,
            no_price=m.no_price,
            question=m.question,
            end_date=m.end_date,
            raw_data=m,
            paired_token_id=m.yes_token_id,
            slug=m.slug,
            event_slug=m.event_slug,
            market_slug=m.market_slug,
            market_url=m.market_url,
        ))

    return opps


# ---------------------------------------------------------------------------
# Strategy B — Cross-market arbitrage
# ---------------------------------------------------------------------------

def _cross_market_confidence(group: list[MarketData], gap: float) -> float:
    """Score how reliable a cross-market signal is (0–1).

    Three factors:
    - count_score:    2-market groups are cleanest; larger groups are penalised
    - gap_score:      gaps of 5–15% are credible; larger gaps look like data artefacts
    - longshot_penalty: any yes_price < 0.05 suggests a corrupted or partial group
    """
    count_score = {2: 1.0, 3: 0.9, 4: 0.75}.get(len(group), 0.5)
    longshot_penalty = 0.3 if any(m.yes_price < 0.05 for m in group) else 0.0
    gap_score = 1.0 if 0.05 <= gap <= 0.15 else (0.8 if gap <= 0.20 else 0.5)
    return max(0.0, count_score * gap_score - longshot_penalty)


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

        # Confidence gate: reject low-quality signals (partial groups, suspicious gaps)
        confidence = _cross_market_confidence(group, gap)
        if confidence < config.CROSS_MARKET_CONFIDENCE_THRESHOLD:
            logger.debug(
                f"Cross-market low confidence: event={event_id} "
                f"confidence={confidence:.2f} gap={gap:.3f}"
            )
            continue

        # BUY the cheapest YES token (most underpriced)
        cheapest = min(group, key=lambda m: m.yes_price)

        logger.debug(
            f"Cross-market arb: event={event_id} total_prob={total_yes_prob:.3f} "
            f"gap={gap:.3f} edge={edge_pct:.1f}% confidence={confidence:.2f}"
        )

        cross_v_score = (edge_pct / 100.0) / max(0.10, 1.0 - cheapest.yes_price)
        opps.append(Opportunity(
            type="cross_market",
            market_id=cheapest.market_id,
            condition_id=cheapest.condition_id,
            token_id=cheapest.yes_token_id,
            side="YES",
            price=cheapest.yes_price,
            edge_pct=edge_pct,
            confidence_source="cross_market",
            yes_price=cheapest.yes_price,
            no_price=cheapest.no_price,
            question=cheapest.question,
            end_date=cheapest.end_date,
            raw_data=cheapest,
            value_score=cross_v_score,
            slug=cheapest.slug,
            event_slug=cheapest.event_slug,
            market_slug=cheapest.market_slug,
            market_url=cheapest.market_url,
        ))

    return opps


# ---------------------------------------------------------------------------
# Strategy C — Odds comparison
# ---------------------------------------------------------------------------

def _is_moneyline_market(market: MarketData) -> bool:
    market_type = (market.sports_market_type or "").strip().lower()
    return market_type == "moneyline"


def _is_unsupported_odds_market(market: MarketData) -> bool:
    text = " ".join([
        market.question,
        market.slug,
        market.market_slug,
    ]).lower()
    blocked_terms = (
        "exact score",
        "o/u",
        "over/under",
        "spread",
        "handicap",
        "total goals",
        "total points",
        "player",
    )
    return any(term in text for term in blocked_terms)


def _odds_match_context(market: MarketData) -> str:
    pieces = [market.event_slug, market.market_slug, market.slug]
    for event in market.events or []:
        for key in ("title", "name", "slug", "ticker"):
            value = event.get(key)
            if value:
                pieces.append(str(value))
    return " ".join(pieces)


def _sportsbook_probability_for_market(
    market: MarketData,
    odds: ExternalOdds,
) -> tuple[Optional[float], str]:
    side = data_feeds.match_team_side(market.question, odds)
    if side == "home":
        return odds.home_prob, "home"
    if side == "away":
        return odds.away_prob, "away"
    if side == "draw":
        return odds.draw_prob, "draw"
    return None, "ambiguous_side"


def _find_odds_comparison_opportunities_legacy(markets: list[MarketData]) -> list[Opportunity]:
    """
    Compare Polymarket YES price with consensus sportsbook probability.
    If sportsbook thinks team wins 60% but Polymarket prices YES at 50¢ → 10% edge.
    """
    if not config.ODDS_API_KEY or not config.ENABLE_ODDS_COMPARISON_ARB:
        return []

    opps: list[Opportunity] = []
    stats: Counter[str] = Counter()

    for m in markets:
        stats["markets"] += 1
        if config.ODDS_COMPARISON_MONEYLINE_ONLY and not _is_moneyline_market(m):
            stats["non_moneyline"] += 1
            continue
        if _is_unsupported_odds_market(m):
            stats["unsupported_market"] += 1
            continue

        ext = data_feeds.get_odds_for_market(
            m.question,
            m.end_date,
            context_text=_odds_match_context(m),
        )
        if ext is None:
            stats["no_odds_match"] += 1
            continue
        stats["matched"] += 1

        # Reject weak fuzzy matches — team name overlap or time proximity too low
        if ext.match_confidence < config.ODDS_MATCH_MIN_CONFIDENCE:
            stats["low_confidence"] += 1
            logger.debug(
                f"Low odds match confidence {ext.match_confidence:.2f}: {m.question[:60]}"
            )
            continue

        # Try to determine if the YES outcome corresponds to the home or away team
        q_lower = m.question.lower()
        home_norm = ext.home_team.lower()
        away_norm = ext.away_team.lower()

        # Simple heuristic: if home team appears first in question
        home_in_q = home_norm.split()[0] in q_lower if ext.home_team else False
        away_in_q = away_norm.split()[0] in q_lower if ext.away_team else False

        sportsbook_prob: Optional[float] = None
        if "draw" in q_lower:
            sportsbook_prob = ext.draw_prob
        elif home_in_q and not away_in_q:
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

        reference_yes_price = _get_cached_best_ask(m.yes_token_id) or m.yes_price
        edge_pct = (sportsbook_prob - reference_yes_price) * 100

        if edge_pct < config.MIN_EDGE_PCT:
            continue

        logger.debug(
            f"Odds arb: {m.question[:50]} | "
            f"polymarket={reference_yes_price:.3f} sportsbook={sportsbook_prob:.3f} edge={edge_pct:.1f}%"
        )

        opps.append(Opportunity(
            type="odds_comparison",
            market_id=m.market_id,
            condition_id=m.condition_id,
            token_id=m.yes_token_id,
            side="YES",
            price=reference_yes_price,
            edge_pct=edge_pct,
            confidence_source="odds_comparison",
            yes_price=reference_yes_price,
            no_price=m.no_price,
            question=m.question,
            end_date=m.end_date,
            raw_data=m,
            external_odds=ext,
            slug=m.slug,
            event_slug=m.event_slug,
            market_slug=m.market_slug,
            market_url=m.market_url,
        ))

    return opps


def _find_odds_comparison_opportunities(markets: list[MarketData]) -> list[Opportunity]:
    """
    Compare Polymarket YES price with consensus sportsbook probability.
    Event context may identify the matchup, but the market question itself
    must identify the exact YES outcome.
    """
    if not config.ODDS_API_KEY or not config.ENABLE_ODDS_COMPARISON_ARB:
        return []

    opps: list[Opportunity] = []
    stats: Counter[str] = Counter()

    for m in markets:
        stats["markets"] += 1
        if config.ODDS_COMPARISON_MONEYLINE_ONLY and not _is_moneyline_market(m):
            stats["non_moneyline"] += 1
            continue
        if _is_unsupported_odds_market(m):
            stats["unsupported_market"] += 1
            continue

        ext = data_feeds.get_odds_for_market(
            m.question,
            m.end_date,
            context_text=_odds_match_context(m),
        )
        if ext is None:
            stats["no_odds_match"] += 1
            continue
        stats["matched"] += 1

        if ext.match_confidence < config.ODDS_MATCH_MIN_CONFIDENCE:
            stats["low_confidence"] += 1
            logger.debug(
                f"Low odds match confidence {ext.match_confidence:.2f}: {m.question[:60]}"
            )
            continue

        sportsbook_prob, side_reason = _sportsbook_probability_for_market(m, ext)
        if sportsbook_prob is None:
            stats[side_reason] += 1
            continue

        reference_yes_price = _get_cached_best_ask(m.yes_token_id) or m.yes_price
        reference_no_price = _get_cached_best_ask(m.no_token_id) or m.no_price
        yes_edge = (sportsbook_prob - reference_yes_price) * 100
        no_true_prob = max(0.0, 1.0 - sportsbook_prob)
        no_edge = (no_true_prob - reference_no_price) * 100

        if yes_edge >= no_edge:
            chosen_side = "YES"
            chosen_token_id = m.yes_token_id
            chosen_price = reference_yes_price
            edge_pct = yes_edge
        else:
            chosen_side = "NO"
            chosen_token_id = m.no_token_id
            chosen_price = reference_no_price
            edge_pct = no_edge

        if chosen_price < config.ODDS_COMPARISON_MIN_PRICE:
            stats["low_price"] += 1
            continue

        if edge_pct < config.MIN_EDGE_PCT:
            stats["below_edge"] += 1
            continue

        # ROI-weighted score: expected return per dollar risked.
        # This prevents longshots from dominating the ranking.
        denominator = max(0.10, 1.0 - chosen_price)
        v_score = edge_pct / 100.0 / denominator

        stats["emitted"] += 1
        logger.debug(
            f"Odds arb: {m.question[:50]} | "
            f"yes={reference_yes_price:.3f}/{sportsbook_prob:.3f} "
            f"no={reference_no_price:.3f}/{no_true_prob:.3f} "
            f"chosen={chosen_side} edge={edge_pct:.1f}% value={v_score:.3f}"
        )

        opps.append(Opportunity(
            type="odds_comparison",
            market_id=m.market_id,
            condition_id=m.condition_id,
            token_id=chosen_token_id,
            side=chosen_side,
            price=chosen_price,
            edge_pct=edge_pct,
            confidence_source="odds_comparison",
            yes_price=reference_yes_price,
            no_price=reference_no_price,
            question=m.question,
            end_date=m.end_date,
            raw_data=m,
            external_odds=ext,
            value_score=v_score,
            slug=m.slug,
            event_slug=m.event_slug,
            market_slug=m.market_slug,
            market_url=m.market_url,
        ))

    logger.info(
        "Odds comparison filters: "
        f"markets={stats['markets']} "
        f"non_moneyline={stats['non_moneyline']} "
        f"unsupported={stats['unsupported_market']} "
        f"matched={stats['matched']} "
        f"no_match={stats['no_odds_match']} "
        f"low_conf={stats['low_confidence']} "
        f"ambiguous={stats['ambiguous_side']} "
        f"low_price={stats['low_price']} "
        f"below_edge={stats['below_edge']} "
        f"emitted={stats['emitted']}"
    )
    return opps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_opportunities(markets: list[MarketData]) -> list[Opportunity]:
    """Run all three arbitrage strategies and return combined opportunities."""
    if not markets:
        return []

    a_opps = _find_same_market_opportunities(markets) if config.ENABLE_SAME_MARKET_ARB else []
    b_opps = _find_cross_market_opportunities(markets) if config.ENABLE_CROSS_MARKET_ARB else []
    c_opps = _find_odds_comparison_opportunities(markets)

    all_opps = a_opps + b_opps + c_opps

    # Deduplicate by token_id + side (prefer highest value score, then edge)
    seen: dict[str, Opportunity] = {}
    for opp in all_opps:
        key = f"{opp.token_id}:{opp.side}"
        if key not in seen or (opp.value_score, opp.edge_pct) > (seen[key].value_score, seen[key].edge_pct):
            seen[key] = opp

    # Rank by value_score (ROI-weighted) so mid-range opportunities beat longshots
    result = sorted(seen.values(), key=lambda o: (o.value_score, o.edge_pct), reverse=True)

    logger.info(
        f"Arbitrage scan: {len(a_opps)} same-market, "
        f"{len(b_opps)} cross-market, {len(c_opps)} odds-comparison "
        f"→ {len(result)} unique opportunities"
    )
    return result
