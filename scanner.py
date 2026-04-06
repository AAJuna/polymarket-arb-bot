"""
Market Scanner — fetches and filters active sports markets from Polymarket Gamma API.
Returns a list of MarketData objects used by all downstream modules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

import config
from logger_setup import get_logger
from utils import TTLCache, parse_iso, retry, seconds_until, utcnow

logger = get_logger(__name__)

GAMMA_MARKETS_URL = f"{config.GAMMA_API_HOST}/markets"
GAMMA_EVENTS_URL  = f"{config.GAMMA_API_HOST}/events"
CLOB_FEE_URL = f"{config.POLYMARKET_HOST}/fee-rate"

_market_cache = TTLCache(ttl_seconds=10)
_fee_cache = TTLCache(ttl_seconds=config.FEE_CACHE_TTL)

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------

@dataclass
class MarketData:
    market_id: str
    condition_id: str
    question: str
    slug: str
    yes_token_id: str
    no_token_id: str
    yes_price: float
    no_price: float
    volume_24h: float
    liquidity: float
    end_date: datetime
    neg_risk: bool
    sports_market_type: str        # e.g. "moneyline", "" if unknown
    fee_rate_yes: float
    fee_rate_no: float
    last_updated: datetime = field(default_factory=utcnow)
    events: list = field(default_factory=list)  # raw event objects from Gamma API
    accepting_orders: bool = True

    @property
    def hours_to_expiry(self) -> float:
        return seconds_until(self.end_date) / 3600.0

    @property
    def is_valid(self) -> bool:
        return (
            self.yes_token_id
            and self.no_token_id
            and self.volume_24h >= config.MIN_VOLUME_24H
            and self.liquidity >= config.MIN_LIQUIDITY
            and self.hours_to_expiry >= config.MIN_HOURS_TO_EXPIRY
            and self.accepting_orders
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_list_field(value) -> list:
    """Handle fields that Gamma API sometimes returns as stringified JSON."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import json as _json
        try:
            parsed = _json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _is_sports_market(raw: dict) -> bool:
    """Return True if this market appears to be sports-related."""
    # Prefer explicit field
    if raw.get("sportsMarketType"):
        return True

    question = (raw.get("question") or "").lower()
    tags = [t.lower() for t in (raw.get("tags") or [])]
    text = question + " " + " ".join(tags)

    return any(kw in text for kw in config.SPORTS_KEYWORDS)


@retry(max_attempts=3, base_delay=1.0, exceptions=(requests.RequestException,))
def _fetch_fee_rate(token_id: str) -> float:
    """Fetch fee rate for a single token from CLOB API."""
    cached = _fee_cache.get(token_id)
    if cached is not None:
        return cached

    resp = _session.get(CLOB_FEE_URL, params={"token_id": token_id}, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    rate = float(data.get("fee_rate", config.DEFAULT_FEE_RATE))
    _fee_cache.set(token_id, rate)
    return rate


def _fetch_fee_rates_bulk(markets_raw: list[dict]) -> dict[str, float]:
    """Return default fee rates for all markets.

    Individual fee-rate fetches are too slow at scale (1300+ sequential HTTP calls).
    Polymarket sports fee is consistently 3% (0.03); we use the configured default.
    Rates are only fetched on-demand in arbitrage.py when needed for precise arb math.
    """
    rates: dict[str, float] = {}
    for raw in markets_raw:
        token_ids = _parse_list_field(raw.get("clobTokenIds"))
        if token_ids:
            rates[token_ids[0]] = config.DEFAULT_FEE_RATE
    return rates


def _parse_market(raw: dict, fee_rates: dict[str, float]) -> Optional[MarketData]:
    """Parse a single Gamma API market dict → MarketData."""
    try:
        token_ids = _parse_list_field(raw.get("clobTokenIds"))
        if len(token_ids) < 2:
            return None

        yes_id, no_id = token_ids[0], token_ids[1]

        outcome_prices = _parse_list_field(raw.get("outcomePrices")) or ["0.5", "0.5"]
        yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.5
        no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5

        end_date_str = raw.get("endDate") or raw.get("end_date_iso") or ""
        if not end_date_str:
            return None
        end_date = parse_iso(end_date_str)

        # Prefer event slug (used in polymarket.com/event/{slug} URLs)
        events = raw.get("events") or []
        event_slug = events[0].get("slug", "") if events else ""
        market_slug = raw.get("slug", "")
        slug = event_slug or market_slug

        return MarketData(
            market_id=str(raw.get("id", "")),
            condition_id=str(raw.get("conditionId", "")),
            question=raw.get("question", ""),
            slug=slug,
            yes_token_id=yes_id,
            no_token_id=no_id,
            yes_price=yes_price,
            no_price=no_price,
            volume_24h=float(raw.get("volume24hr") or raw.get("volume") or 0),
            liquidity=float(raw.get("liquidity") or 0),
            end_date=end_date,
            neg_risk=bool(raw.get("negRisk", False)),
            sports_market_type=raw.get("sportsMarketType") or "",
            fee_rate_yes=fee_rates.get(yes_id, config.DEFAULT_FEE_RATE),
            fee_rate_no=fee_rates.get(yes_id, config.DEFAULT_FEE_RATE),  # same contract
            events=raw.get("events") or [],
            accepting_orders=bool(raw.get("acceptingOrders", True)),
        )
    except Exception as e:
        logger.debug(f"Failed to parse market {raw.get('id')}: {e}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@retry(max_attempts=3, base_delay=2.0, exceptions=(requests.RequestException,))
def _fetch_page(offset: int, limit: int = 100) -> list[dict]:
    params = {
        "active": "true",
        "limit": limit,
        "offset": offset,
        "order": "volume24hr",
        "ascending": "false",
    }
    resp = _session.get(GAMMA_MARKETS_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


@retry(max_attempts=3, base_delay=2.0, exceptions=(requests.RequestException,))
def _fetch_sports_events() -> list[dict]:
    """Fetch markets from the events endpoint with tag=sports — matches /sports/live page."""
    markets = []
    try:
        resp = _session.get(
            GAMMA_EVENTS_URL,
            params={"active": "true", "tag": "sports", "limit": 100, "order": "volume24hr", "ascending": "false"},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json()
        for event in events:
            event_slug = event.get("slug", "")
            for m in (event.get("markets") or []):
                m["events"] = [event]   # inject event so slug is accessible
                m["_event_slug"] = event_slug
                markets.append(m)
    except Exception as e:
        logger.warning(f"Sports events fetch failed: {e}")
    return markets


def scan_sports_markets() -> list[MarketData]:
    """Scan all active Polymarket sports markets. Results cached for 10 seconds."""
    cached = _market_cache.get("markets")
    if cached is not None:
        logger.debug(f"Returning {len(cached)} markets from cache")
        return cached

    logger.debug("Scanning Polymarket sports markets...")
    raw_markets: list[dict] = []
    offset = 0
    limit = 100

    while True:
        try:
            page = _fetch_page(offset, limit)
        except Exception as e:
            logger.error(f"Gamma API page fetch failed (offset={offset}): {e}")
            break

        if not page:
            break

        raw_markets.extend(page)

        if len(page) < limit:
            break
        last_volume = float(page[-1].get("volume24hr") or 0)
        if last_volume < config.MIN_VOLUME_24H:
            break

        offset += limit
        time.sleep(0.2)

    # Also fetch from /events?tag=sports (matches /sports/live page)
    sports_event_markets = _fetch_sports_events()

    # Merge — deduplicate by market id
    seen_ids = {r.get("id") for r in raw_markets}
    for m in sports_event_markets:
        if m.get("id") not in seen_ids:
            raw_markets.append(m)
            seen_ids.add(m.get("id"))

    # Filter sports only
    sports_raw = [r for r in raw_markets if _is_sports_market(r)]
    logger.info(f"Found {len(sports_raw)} sports markets out of {len(raw_markets)} total")

    if not sports_raw:
        return []

    # Fetch fee rates
    fee_rates = _fetch_fee_rates_bulk(sports_raw)

    # Parse
    markets: list[MarketData] = []
    for raw in sports_raw:
        m = _parse_market(raw, fee_rates)
        if m and m.is_valid:
            markets.append(m)

    logger.info(f"Returning {len(markets)} valid sports markets after filtering")
    _market_cache.set("markets", markets)
    return markets


def get_market_status(condition_id: str) -> dict:
    """Fetch resolution status for a specific market."""
    try:
        resp = _session.get(
            f"{config.GAMMA_API_HOST}/markets",
            params={"condition_id": condition_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data:
            return data[0] if isinstance(data, list) else data
    except Exception as e:
        logger.error(f"Failed to fetch market status for {condition_id}: {e}")
    return {}
