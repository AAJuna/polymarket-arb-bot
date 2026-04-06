"""
Market Scanner — fetches and filters active sports markets from Polymarket Gamma API.
Returns a list of MarketData objects used by all downstream modules.
"""

from __future__ import annotations

from collections import Counter
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

import config
from logger_setup import get_logger
from utils import TTLCache, format_time_remaining, parse_iso, retry, seconds_until, utcnow

logger = get_logger(__name__)

GAMMA_MARKETS_URL = f"{config.GAMMA_API_HOST}/markets"
GAMMA_EVENTS_URL  = f"{config.GAMMA_API_HOST}/events"
GAMMA_PUBLIC_SEARCH_URL = f"{config.GAMMA_API_HOST}/public-search"
CLOB_FEE_URL = f"{config.POLYMARKET_HOST}/fee-rate"

_market_cache = TTLCache(ttl_seconds=10)
_fee_cache = TTLCache(ttl_seconds=config.FEE_CACHE_TTL)
_market_status_cache = TTLCache(ttl_seconds=15)

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
    event_slug: str
    market_slug: str
    market_url: str
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
    fee_rate_bps: int = 0
    fees_enabled: bool = False
    order_price_min_tick_size: float = 0.01
    order_min_size: float = 0.0
    is_live: bool = False
    active: bool = True
    closed: bool = False
    archived: bool = False
    enable_order_book: bool = True
    accepting_orders: bool = True
    last_updated: datetime = field(default_factory=utcnow)
    events: list = field(default_factory=list)  # raw event objects from Gamma API

    @property
    def is_open(self) -> bool:
        if not self.active or self.closed or self.archived:
            return False
        if not self.enable_order_book or not self.accepting_orders:
            return False
        if seconds_until(self.end_date) <= 0:
            return False
        if self.events and not any(_is_event_open(event) for event in self.events):
            return False
        return True

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
            and self.is_open
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


def _coerce_flag(raw: dict, key: str, default: bool) -> bool:
    value = raw.get(key)
    if value is None:
        return default
    return bool(value)


def _coerce_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _extract_end_date(raw: dict) -> Optional[datetime]:
    end_date_str = raw.get("endDate") or raw.get("endDateIso") or raw.get("end_date_iso") or ""
    if not end_date_str:
        return None

    try:
        return parse_iso(end_date_str)
    except Exception:
        return None


def _extract_event_end_date(raw: dict) -> Optional[datetime]:
    events = raw.get("events") or []
    end_dates = [date for date in (_extract_end_date(event) for event in events) if date is not None]
    if not end_dates:
        return None
    return min(end_dates)


def _extract_effective_end_date(raw: dict) -> Optional[datetime]:
    return _extract_end_date(raw) or _extract_event_end_date(raw)


def _build_market_url(
    condition_id: str,
    event_slug: str = "",
    market_slug: str = "",
    legacy_slug: str = "",
) -> str:
    if event_slug.endswith("-more-markets"):
        base_event_slug = event_slug[: -len("-more-markets")]
        if not market_slug or market_slug.startswith(base_event_slug):
            event_slug = base_event_slug

    if event_slug and market_slug:
        return f"https://polymarket.com/event/{event_slug}/{market_slug}"
    if legacy_slug:
        return f"https://polymarket.com/event/{legacy_slug}"
    if condition_id:
        return f"https://polymarket.com/predictions?conditionId={condition_id}"
    return "https://polymarket.com/predictions"


def _is_event_open(event: dict) -> bool:
    if event.get("active") is False:
        return False
    if bool(event.get("closed")) or bool(event.get("archived")):
        return False

    end_date = _extract_effective_end_date(event)
    if end_date is not None and seconds_until(end_date) <= 0:
        return False

    return True


def _extract_market_fee(raw: dict) -> tuple[float, int]:
    """Return fee rate as decimal and basis points for a market payload."""
    if raw.get("feesEnabled") is False:
        return 0.0, 0

    fee_schedule = raw.get("feeSchedule") or {}
    if isinstance(fee_schedule, dict):
        rate = fee_schedule.get("rate")
        if rate is not None:
            rate_decimal = _coerce_float(rate, 0.0)
            return rate_decimal, int(round(rate_decimal * 10_000))

    for key in ("fee_rate", "feeRate", "base_fee", "baseFee"):
        value = raw.get(key)
        if value is None:
            continue
        numeric = _coerce_float(value, 0.0)
        if numeric <= 0:
            continue
        if numeric > 1:
            return numeric / 10_000.0, int(round(numeric))
        return numeric, int(round(numeric * 10_000))

    return config.DEFAULT_FEE_RATE, int(round(config.DEFAULT_FEE_RATE * 10_000))


def evaluate_market_status(raw: dict) -> tuple[bool, str]:
    """Return whether a market is still open for trading plus the first failing reason."""
    end_date = _extract_effective_end_date(raw)
    if end_date is None:
        return False, "missing_end_date"

    if raw.get("active") is False:
        return False, "inactive"
    if bool(raw.get("closed")):
        return False, "closed"
    if bool(raw.get("archived")):
        return False, "archived"
    if raw.get("acceptingOrders") is False:
        return False, "not_accepting_orders"
    if raw.get("enableOrderBook") is False:
        return False, "orderbook_disabled"
    if seconds_until(end_date) <= 0:
        return False, "expired"

    events = raw.get("events") or []
    if events and not any(_is_event_open(event) for event in events):
        return False, "event_closed"

    return True, "open"


def summarize_market_window(raw: dict) -> dict:
    """Return a compact market window summary from official Gamma fields."""
    events = raw.get("events") or []
    event_slug = raw.get("_event_slug") or (events[0].get("slug", "") if events else "")
    market_slug = raw.get("slug", "")
    condition_id = str(raw.get("conditionId", "") or "")
    end_date = _extract_effective_end_date(raw)
    is_open, reason = evaluate_market_status(raw)

    if end_date is None:
        ends_in = "Ends unknown"
        end_date_iso = ""
        seconds_remaining = None
    else:
        remaining = seconds_until(end_date)
        seconds_remaining = remaining
        end_date_iso = end_date.isoformat()
        ends_in = "Ended" if remaining <= 0 else f"Ends in {format_time_remaining(end_date)}"

    return {
        "market_id": str(raw.get("id", "") or raw.get("conditionId", "") or market_slug or raw.get("question", "")),
        "condition_id": condition_id,
        "question": str(raw.get("question", "") or ""),
        "event_slug": event_slug,
        "market_slug": market_slug,
        "market_url": _build_market_url(
            condition_id=condition_id,
            event_slug=event_slug,
            market_slug=market_slug,
            legacy_slug=market_slug or event_slug,
        ),
        "end_date": end_date_iso,
        "ends_in": ends_in,
        "seconds_remaining": seconds_remaining,
        "is_open": is_open,
        "reason": reason,
        "raw": raw,
    }


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
    rate, _ = _extract_market_fee(data)
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
        fee_rate, _ = _extract_market_fee(raw)
        token_ids = _parse_list_field(raw.get("clobTokenIds"))
        if token_ids:
            rates[token_ids[0]] = fee_rate
        if len(token_ids) > 1:
            rates[token_ids[1]] = fee_rate
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

        end_date = _extract_effective_end_date(raw)
        if end_date is None:
            return None

        # Prefer explicit event + market slugs for direct market URLs.
        events = raw.get("events") or []
        event_slug = raw.get("_event_slug") or (events[0].get("slug", "") if events else "")
        market_slug = raw.get("slug", "")
        slug = market_slug or event_slug
        market_url = _build_market_url(
            condition_id=str(raw.get("conditionId", "")),
            event_slug=event_slug,
            market_slug=market_slug,
            legacy_slug=slug,
        )

        return MarketData(
            market_id=str(raw.get("id", "")),
            condition_id=str(raw.get("conditionId", "")),
            question=raw.get("question", ""),
            slug=slug,
            event_slug=event_slug,
            market_slug=market_slug,
            market_url=market_url,
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
            fee_rate_no=fee_rates.get(no_id, config.DEFAULT_FEE_RATE),
            fee_rate_bps=_extract_market_fee(raw)[1],
            fees_enabled=bool(raw.get("feesEnabled", True)),
            order_price_min_tick_size=_coerce_float(raw.get("orderPriceMinTickSize"), 0.01),
            order_min_size=_coerce_float(raw.get("orderMinSize"), 0.0),
            is_live=bool(raw.get("_event_live")) or any(bool(event.get("live")) for event in events),
            active=_coerce_flag(raw, "active", True),
            closed=_coerce_flag(raw, "closed", False),
            archived=_coerce_flag(raw, "archived", False),
            enable_order_book=_coerce_flag(raw, "enableOrderBook", True),
            accepting_orders=_coerce_flag(raw, "acceptingOrders", True),
            events=raw.get("events") or [],
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
        "closed": "false",
        "limit": limit,
        "offset": offset,
        "order": "volume24hr",
        "ascending": "false",
    }
    resp = _session.get(GAMMA_MARKETS_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


@retry(max_attempts=3, base_delay=2.0, exceptions=(requests.RequestException,))
def _fetch_events_page(offset: int, limit: int = 100, live_only: bool = False) -> list[dict]:
    params = {
        "active": "true",
        "closed": "false",
        "tag": "sports",
        "limit": limit,
        "offset": offset,
        "order": "volume24hr",
        "ascending": "false",
    }
    if live_only:
        params["live"] = "true"

    resp = _session.get(GAMMA_EVENTS_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _flatten_event_markets(events: list[dict], live_only: bool = False) -> list[dict]:
    markets: list[dict] = []
    for event in events:
        event_slug = event.get("slug", "")
        is_live_event = live_only or bool(event.get("live"))
        for market in (event.get("markets") or []):
            enriched_market = dict(market)
            enriched_market["events"] = [event]
            enriched_market["_event_slug"] = event_slug
            enriched_market["_event_live"] = is_live_event
            markets.append(enriched_market)
    return markets


def _fetch_sports_event_markets(live_only: bool = False) -> list[dict]:
    """Fetch sports event markets, optionally limited to live events from /sports/live."""
    markets: list[dict] = []
    offset = 0
    limit = 100
    source_label = "/sports/live" if live_only else "/sports"
    max_pages = None if live_only else 1
    pages_fetched = 0

    while True:
        try:
            events = _fetch_events_page(offset=offset, limit=limit, live_only=live_only)
        except Exception as e:
            logger.warning(f"Sports events fetch failed for {source_label} (offset={offset}): {e}")
            break

        if not events:
            break

        markets.extend(_flatten_event_markets(events, live_only=live_only))
        pages_fetched += 1

        if len(events) < limit:
            break
        if max_pages is not None and pages_fetched >= max_pages:
            break

        offset += limit
        time.sleep(0.2)

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

    # Enrich with sports event metadata and explicitly include /sports/live markets.
    sports_event_markets = _fetch_sports_event_markets()
    live_sports_event_markets = _fetch_sports_event_markets(live_only=True)

    # Merge — deduplicate by market id
    raw_by_id = {r.get("id"): r for r in raw_markets}
    seen_ids = set(raw_by_id.keys())
    for m in sports_event_markets + live_sports_event_markets:
        market_id = m.get("id")
        if market_id in seen_ids:
            existing = raw_by_id.get(market_id) or {}
            if m.get("events"):
                existing["events"] = m.get("events")
            if m.get("_event_slug") and not existing.get("_event_slug"):
                existing["_event_slug"] = m.get("_event_slug")
            if m.get("_event_live"):
                existing["_event_live"] = True
            continue
        if market_id not in seen_ids:
            raw_markets.append(m)
            raw_by_id[market_id] = m
            seen_ids.add(market_id)

    # Filter sports only
    sports_raw = [r for r in raw_markets if _is_sports_market(r)]
    logger.info(f"Found {len(sports_raw)} sports markets out of {len(raw_markets)} total")

    if not sports_raw:
        return []

    open_sports_raw: list[dict] = []
    rejected_statuses: Counter[str] = Counter()
    for raw in sports_raw:
        is_open, reason = evaluate_market_status(raw)
        if not is_open:
            rejected_statuses[reason] += 1
            continue
        open_sports_raw.append(raw)

    if rejected_statuses:
        breakdown = ", ".join(
            f"{reason}={count}" for reason, count in rejected_statuses.most_common(5)
        )
        logger.info(
            f"Filtered {sum(rejected_statuses.values())} sports markets that are not open "
            f"({breakdown})"
        )

    if not open_sports_raw:
        return []

    # Fetch fee rates
    fee_rates = _fetch_fee_rates_bulk(open_sports_raw)

    # Parse
    markets: list[MarketData] = []
    for raw in open_sports_raw:
        m = _parse_market(raw, fee_rates)
        if m and m.is_valid:
            markets.append(m)

    live_count = sum(1 for market in markets if market.is_live)
    if live_count:
        logger.info(f"Included {live_count} live sports markets from /sports/live")

    logger.info(f"Returning {len(markets)} valid sports markets after filtering")
    _market_cache.set("markets", markets)
    return markets


def get_market_status(condition_id: str) -> dict:
    """Fetch resolution status for a specific market."""
    cached = _market_status_cache.get(condition_id)
    if cached is not None:
        return cached

    try:
        resp = _session.get(
            GAMMA_MARKETS_URL,
            params={"condition_ids": condition_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data:
            market = data[0] if isinstance(data, list) else data
            _market_status_cache.set(condition_id, market)
            return market
    except Exception as e:
        logger.error(f"Failed to fetch market status for {condition_id}: {e}")
    return {}


@retry(max_attempts=3, base_delay=1.0, exceptions=(requests.RequestException,))
def _public_search(query: str, limit_per_type: int = 10, active_only: bool = True) -> dict:
    params = {
        "q": query,
        "limit_per_type": limit_per_type,
        "optimized": "true",
        "search_profiles": "false",
        "search_tags": "false",
        "page": 1,
    }
    if active_only:
        params["events_status"] = "active"
        params["keep_closed_markets"] = 0

    resp = _session.get(GAMMA_PUBLIC_SEARCH_URL, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def search_market_windows(query: str, limit_per_type: int = 10, active_only: bool = True) -> list[dict]:
    """Search markets by keyword using official Gamma public-search and summarize expiry."""
    query = (query or "").strip()
    if not query:
        return []

    payload = _public_search(query, limit_per_type=limit_per_type, active_only=active_only)
    results: list[dict] = []
    seen_market_ids: set[str] = set()

    for event in payload.get("events") or []:
        event_slug = event.get("slug", "")
        for market in event.get("markets") or []:
            market_id = str(
                market.get("id", "") or
                market.get("conditionId", "") or
                market.get("slug", "") or
                market.get("question", "")
            )
            if not market_id or market_id in seen_market_ids:
                continue
            enriched_market = dict(market)
            enriched_market["events"] = [event]
            if event_slug and not enriched_market.get("_event_slug"):
                enriched_market["_event_slug"] = event_slug
            summary = summarize_market_window(enriched_market)
            if active_only and not summary["is_open"]:
                continue
            results.append(summary)
            seen_market_ids.add(market_id)

    return results


def verify_market_open(condition_id: str, keyword: str = "") -> tuple[bool, dict, str]:
    """Fetch the latest Gamma payload and verify the market is still open.

    Falls back to official /public-search by keyword only if the direct status lookup misses.
    """
    status = get_market_status(condition_id)
    if status:
        is_open, reason = evaluate_market_status(status)
        return is_open, status, reason

    keyword = (keyword or "").strip()
    if keyword:
        try:
            normalized_keyword = _normalize_text(keyword)
            for match in search_market_windows(keyword, limit_per_type=10, active_only=False):
                if _normalize_text(str(match.get("question", ""))) == normalized_keyword:
                    logger.debug(
                        f"verify_market_open fallback matched via public-search for {condition_id}"
                    )
                    raw = match.get("raw") or {}
                    return bool(match.get("is_open")), raw, str(match.get("reason") or "status_unavailable")
        except Exception as e:
            logger.debug(f"public-search fallback failed for {condition_id}: {e}")

    return False, {}, "status_unavailable"
