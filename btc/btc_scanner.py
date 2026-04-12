"""
BTC 5-minute market scanner for Polymarket.

Scans Gamma API for active "Bitcoin Up or Down" 5-minute markets and
tracks window start/end times.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from btc import config_btc as cfg
from logger_setup import get_logger

logger = get_logger(__name__)

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})


@dataclass
class BtcMarket:
    """A single BTC 5-minute prediction market."""

    condition_id: str
    market_id: str
    question: str
    up_token_id: str
    down_token_id: str
    up_price: float
    down_price: float
    window_start: datetime
    window_end: datetime
    volume: float
    liquidity: float
    tick_size: float
    neg_risk: bool
    accepting_orders: bool
    raw: dict


class BtcScanner:
    """Scan and cache active BTC 5-minute markets from Gamma API."""

    def __init__(self) -> None:
        self._cache: Optional[list[BtcMarket]] = None
        self._cache_time: float = 0.0

    def scan_markets(self) -> list[BtcMarket]:
        """Fetch active BTC 5-min markets. Uses cache if fresh."""
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_time) < cfg.SCANNER_CACHE_TTL:
            return self._cache

        markets = self._fetch_markets()
        self._cache = markets
        self._cache_time = now
        return markets

    def get_current_window(self) -> Optional[BtcMarket]:
        """Return the market whose 5-min window is currently active."""
        now = datetime.now(timezone.utc)
        for m in self.scan_markets():
            if m.window_start <= now < m.window_end and m.accepting_orders:
                return m
        return None

    def get_next_window(self) -> Optional[BtcMarket]:
        """Return the soonest upcoming market (window_start > now)."""
        now = datetime.now(timezone.utc)
        upcoming = [
            m for m in self.scan_markets()
            if m.window_start > now
        ]
        if not upcoming:
            return None
        return min(upcoming, key=lambda m: m.window_start)

    def get_tradeable_window(self) -> Optional[BtcMarket]:
        """Return current window if still within entry deadline, else next window."""
        current = self.get_current_window()
        if current:
            now = datetime.now(timezone.utc)
            elapsed = (now - current.window_start).total_seconds()
            if elapsed <= cfg.ENTRY_DEADLINE_SEC:
                return current
        return self.get_next_window()

    def invalidate_cache(self) -> None:
        self._cache = None
        self._cache_time = 0.0

    # ------------------------------------------------------------------
    # Gamma API
    # ------------------------------------------------------------------

    def _fetch_markets(self) -> list[BtcMarket]:
        """Fetch BTC 5-min markets from Gamma API."""
        markets: list[BtcMarket] = []

        try:
            # Search for BTC Up or Down 5-min series via tag_slug
            url = f"{cfg.GAMMA_API_HOST}/events"
            params = {
                "active": "true",
                "closed": "false",
                "limit": "50",
                "order": "startDate",
                "ascending": "false",
                "tag_slug": "up-or-down",
            }
            resp = _session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            events = resp.json()

            for event in events:
                title = event.get("title", "")
                slug = event.get("slug", "")
                # Only BTC 5-min markets
                if "bitcoin" not in title.lower():
                    continue
                if "5m" not in slug and "5 minute" not in title.lower():
                    continue

                for raw_market in event.get("markets", []):
                    parsed = self._parse_market(raw_market, event)
                    if parsed:
                        markets.append(parsed)

        except requests.RequestException as e:
            logger.warning(f"BTC scanner fetch failed: {e}")
            return self._cache or []

        logger.debug(f"BTC scanner: found {len(markets)} active 5-min markets")
        return markets

    def _parse_market(self, raw: dict, event: dict) -> Optional[BtcMarket]:
        """Parse a raw Gamma API market dict into a BtcMarket."""
        try:
            outcomes_raw = raw.get("outcomes", [])
            if isinstance(outcomes_raw, str):
                import json as _json
                outcomes = _json.loads(outcomes_raw)
            else:
                outcomes = outcomes_raw
            if len(outcomes) < 2:
                return None

            # Validate this is an Up/Down market
            outcomes_lower = [o.lower() for o in outcomes]
            if "up" not in outcomes_lower or "down" not in outcomes_lower:
                return None

            up_idx = outcomes_lower.index("up")
            down_idx = outcomes_lower.index("down")

            token_ids_raw = raw.get("clobTokenIds", [])
            if isinstance(token_ids_raw, str):
                import json as _json
                token_ids = _json.loads(token_ids_raw)
            else:
                token_ids = token_ids_raw
            if len(token_ids) < 2:
                return None

            prices_raw = raw.get("outcomePrices", ["0.5", "0.5"])
            if isinstance(prices_raw, str):
                import json as _json
                prices = _json.loads(prices_raw)
            else:
                prices = prices_raw
            if len(prices) < 2:
                prices = ["0.5", "0.5"]

            # Parse times
            event_start_str = raw.get("eventStartTime") or event.get("startDate", "")
            end_date_str = raw.get("endDate", "")

            if not event_start_str or not end_date_str:
                return None

            window_start = datetime.fromisoformat(
                event_start_str.replace("Z", "+00:00")
            )
            window_end = datetime.fromisoformat(
                end_date_str.replace("Z", "+00:00")
            )

            return BtcMarket(
                condition_id=raw.get("conditionId", ""),
                market_id=str(raw.get("id", "")),
                question=raw.get("question", event.get("title", "")),
                up_token_id=token_ids[up_idx],
                down_token_id=token_ids[down_idx],
                up_price=float(prices[up_idx]),
                down_price=float(prices[down_idx]),
                window_start=window_start,
                window_end=window_end,
                volume=float(raw.get("volumeNum", 0)),
                liquidity=float(raw.get("liquidityNum", 0)),
                tick_size=float(raw.get("orderPriceMinTickSize", 0.01)),
                neg_risk=bool(raw.get("negRisk", False)),
                accepting_orders=bool(raw.get("acceptingOrders", False)),
                raw=raw,
            )
        except (ValueError, KeyError, IndexError) as e:
            logger.debug(f"Failed to parse BTC market: {e}")
            return None
