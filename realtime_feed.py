"""
Realtime market feed for Polymarket Market Channel.

Keeps a background WebSocket connection alive, maintains a bounded watchlist of
token IDs, and exposes fresh best bid/ask plus full ask ladders when available.
HTTP polling remains the fallback path if the feed is unavailable.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

import config
from logger_setup import get_logger
from utils import utcnow

logger = get_logger(__name__)

DATA_DIR = Path("data")
STATUS_FILE = DATA_DIR / "realtime_feed_status.json"

if TYPE_CHECKING:
    from scanner import MarketData


@dataclass
class QuoteSnapshot:
    asset_id: str
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    bids: list = field(default_factory=list)
    asks: list = field(default_factory=list)
    spread: Optional[float] = None
    last_trade_price: Optional[float] = None
    quote_updated_monotonic: float = 0.0
    book_updated_monotonic: float = 0.0
    timestamp_ms: int = 0


class RealtimeMarketFeed:
    """Optional Polymarket Market Channel watcher with dynamic subscriptions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._subscription_dirty = threading.Event()
        self._connected = False
        self._desired_assets: set[str] = set()
        self._subscribed_assets: set[str] = set()
        self._quotes: dict[str, QuoteSnapshot] = {}
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._websocket_module = None
        self._last_message_monotonic: float = 0.0
        self._message_count = 0
        self._reconnect_count = 0
        self._last_status_write = 0.0
        DATA_DIR.mkdir(exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(config.REALTIME_MARKET_WS_ENABLED)

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def get_spread(self, asset_id: str) -> Optional[float]:
        snapshot = self._get_snapshot(asset_id)
        if snapshot is None:
            return None
        age = time.monotonic() - snapshot.quote_updated_monotonic
        if age > config.REALTIME_MARKET_WS_QUOTE_TTL_SECONDS:
            return None
        if snapshot.spread is not None:
            return snapshot.spread
        if snapshot.best_bid is None or snapshot.best_ask is None:
            return None
        return max(0.0, snapshot.best_ask - snapshot.best_bid)

    def status_snapshot(self) -> dict:
        with self._lock:
            watched = len(self._desired_assets)
            quotes = len(self._quotes)
            connected = self._connected
        last_message_age = (
            time.monotonic() - self._last_message_monotonic
            if self._last_message_monotonic
            else None
        )
        return {
            "enabled": self.enabled,
            "connected": connected,
            "watched_assets": watched,
            "quote_cache_size": quotes,
            "message_count": self._message_count,
            "reconnect_count": self._reconnect_count,
            "last_message_age_seconds": round(last_message_age, 3) if last_message_age is not None else None,
            "updated_at": utcnow().isoformat(),
        }

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return

        try:
            import websocket  # websocket-client
        except Exception as e:
            logger.warning(f"Realtime market feed disabled: websocket-client unavailable ({e})")
            self._persist_status(force=True)
            return

        self._websocket_module = websocket
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="polymarket-market-ws",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Realtime market feed enabled "
            f"(max_assets={config.REALTIME_MARKET_WS_MAX_ASSETS}, "
            f"window={config.REALTIME_MARKET_WS_MAX_HOURS_TO_EXPIRY:.0f}h)"
        )
        self._persist_status(force=True)

    def stop(self) -> None:
        self._stop_event.set()
        self._subscription_dirty.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._heartbeat_thread = None
        self._thread = None
        self._ws = None
        self._connected = False
        self._persist_status(force=True)

    def refresh_watchlist(
        self,
        markets: Optional[list["MarketData"]] = None,
        open_positions: Optional[Iterable[dict]] = None,
    ) -> int:
        if not self.enabled:
            return 0

        asset_ids: list[str] = []
        seen: set[str] = set()

        def add(asset_id: Optional[str]) -> None:
            asset_id = str(asset_id or "").strip()
            if not asset_id or asset_id in seen:
                return
            if len(asset_ids) >= config.REALTIME_MARKET_WS_MAX_ASSETS:
                return
            seen.add(asset_id)
            asset_ids.append(asset_id)

        for pos in open_positions or []:
            add(pos.get("token_id"))

        candidate_markets = list(markets or [])
        if candidate_markets:
            near_expiry = sorted(
                [
                    market for market in candidate_markets
                    if market.hours_to_expiry <= config.REALTIME_MARKET_WS_MAX_HOURS_TO_EXPIRY
                ],
                key=lambda market: (
                    market.hours_to_expiry,
                    -market.liquidity,
                    -market.volume_24h,
                ),
            )
            for market in near_expiry:
                add(market.yes_token_id)
                add(market.no_token_id)
                if len(asset_ids) >= config.REALTIME_MARKET_WS_MAX_ASSETS:
                    break

            if len(asset_ids) < config.REALTIME_MARKET_WS_MAX_ASSETS:
                liquid_markets = sorted(
                    candidate_markets,
                    key=lambda market: (
                        -market.liquidity,
                        -market.volume_24h,
                        market.hours_to_expiry,
                    ),
                )
                for market in liquid_markets:
                    add(market.yes_token_id)
                    if len(asset_ids) >= config.REALTIME_MARKET_WS_MAX_ASSETS:
                        break

        self.update_assets(asset_ids)
        return len(asset_ids)

    def update_assets(self, asset_ids: Iterable[str]) -> None:
        cleaned = {
            str(asset_id).strip()
            for asset_id in asset_ids
            if str(asset_id or "").strip()
        }
        if len(cleaned) > config.REALTIME_MARKET_WS_MAX_ASSETS:
            cleaned = set(sorted(cleaned)[: config.REALTIME_MARKET_WS_MAX_ASSETS])

        with self._lock:
            if cleaned == self._desired_assets:
                return
            self._desired_assets = cleaned
        self._subscription_dirty.set()
        self._persist_status()

    def get_best_ask(self, asset_id: str) -> Optional[float]:
        snapshot = self._get_snapshot(asset_id)
        if snapshot is None or snapshot.best_ask is None:
            return None
        age = time.monotonic() - snapshot.quote_updated_monotonic
        if age > config.REALTIME_MARKET_WS_QUOTE_TTL_SECONDS:
            return None
        return snapshot.best_ask

    def get_best_bid(self, asset_id: str) -> Optional[float]:
        snapshot = self._get_snapshot(asset_id)
        if snapshot is None or snapshot.best_bid is None:
            return None
        age = time.monotonic() - snapshot.quote_updated_monotonic
        if age > config.REALTIME_MARKET_WS_QUOTE_TTL_SECONDS:
            return None
        return snapshot.best_bid

    def get_orderbook_asks(self, asset_id: str) -> list:
        snapshot = self._get_snapshot(asset_id)
        if snapshot is None or not snapshot.asks:
            return []
        age = time.monotonic() - snapshot.book_updated_monotonic
        if age > config.REALTIME_MARKET_WS_BOOK_TTL_SECONDS:
            return []
        return [dict(level) for level in snapshot.asks]

    def get_quote_updated_monotonic(self, asset_id: str) -> float:
        snapshot = self._get_snapshot(asset_id)
        return snapshot.quote_updated_monotonic if snapshot else 0.0

    def log_status(self) -> None:
        if not self.enabled:
            return
        status = self.status_snapshot()
        last_message_age = status.get("last_message_age_seconds")
        last_message_label = f"{last_message_age:.1f}s" if last_message_age is not None else "n/a"
        logger.info(
            "Realtime feed: "
            f"connected={status['connected']} watched={status['watched_assets']} "
            f"quotes={status['quote_cache_size']} messages={status['message_count']} "
            f"reconnects={status['reconnect_count']} "
            f"last_message={last_message_label}"
        )
        self._persist_status(force=True)

    def _get_snapshot(self, asset_id: str) -> Optional[QuoteSnapshot]:
        with self._lock:
            return self._quotes.get(str(asset_id))

    def _persist_status(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_status_write) < 5.0:
            return

        payload = self.status_snapshot()
        tmp_file = STATUS_FILE.with_suffix(".json.tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
            try:
                tmp_file.replace(STATUS_FILE)
            except PermissionError:
                with open(STATUS_FILE, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            self._last_status_write = now
        except Exception as e:
            logger.debug(f"Realtime status write failed: {e}")

    def _run(self) -> None:
        backoff = 2.0
        while not self._stop_event.is_set():
            desired_assets = self._get_desired_assets()
            if not desired_assets:
                time.sleep(1.0)
                continue

            websocket = self._websocket_module
            if websocket is None:
                return

            try:
                ws_app = websocket.WebSocketApp(
                    config.REALTIME_MARKET_WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws = ws_app
                ws_app.run_forever()
                backoff = 2.0
            except Exception as e:
                logger.warning(f"Realtime market feed connection failed: {e}")
            finally:
                self._connected = False
                self._ws = None
                with self._lock:
                    self._subscribed_assets.clear()
                self._persist_status(force=True)

            if self._stop_event.is_set():
                break

            self._reconnect_count += 1
            time.sleep(min(backoff, 30.0))
            backoff *= 2.0

    def _on_open(self, ws_app) -> None:
        self._connected = True
        self._subscription_dirty.set()
        self._persist_status(force=True)
        logger.info(
            "Realtime market feed connected "
            f"({len(self._get_desired_assets())} asset(s) queued)"
        )
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(ws_app,),
            name="polymarket-market-ws-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _on_message(self, ws_app, message: str) -> None:
        self._last_message_monotonic = time.monotonic()
        self._message_count += 1
        self._persist_status()

        if message == "PONG":
            return
        if message.lower() == "ping":
            self._safe_send_text(ws_app, "pong")
            return

        try:
            payload = json.loads(message)
        except Exception:
            logger.debug(f"Realtime market feed ignored non-JSON message: {message!r}")
            return

        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    self._handle_event(item)
            return

        if isinstance(payload, dict):
            self._handle_event(payload)

    def _on_error(self, ws_app, error) -> None:
        if self._stop_event.is_set():
            return
        logger.warning(f"Realtime market feed error: {error}")

    def _on_close(self, ws_app, status_code, message) -> None:
        if not self._stop_event.is_set():
            logger.warning(
                "Realtime market feed closed "
                f"(status={status_code}, message={message})"
            )
        self._connected = False
        self._persist_status(force=True)

    def _heartbeat_loop(self, ws_app) -> None:
        next_ping = time.monotonic() + 10.0
        while not self._stop_event.is_set():
            if not getattr(ws_app, "sock", None) or not ws_app.sock or not ws_app.sock.connected:
                return

            if self._subscription_dirty.is_set():
                self._flush_subscription_changes(ws_app)

            now = time.monotonic()
            if now >= next_ping:
                self._safe_send_text(ws_app, "PING")
                next_ping = now + 10.0

            self._persist_status()
            time.sleep(1.0)

    def _handle_event(self, payload: dict) -> None:
        event_type = str(payload.get("event_type") or "").strip()
        if not event_type:
            return

        if event_type == "book":
            self._update_book(payload)
            return

        if event_type == "price_change":
            for change in payload.get("price_changes") or []:
                if isinstance(change, dict):
                    self._update_quote(
                        asset_id=change.get("asset_id"),
                        best_bid=change.get("best_bid"),
                        best_ask=change.get("best_ask"),
                        spread=None,
                        timestamp_ms=payload.get("timestamp"),
                    )
            return

        if event_type == "best_bid_ask":
            self._update_quote(
                asset_id=payload.get("asset_id"),
                best_bid=payload.get("best_bid"),
                best_ask=payload.get("best_ask"),
                spread=payload.get("spread"),
                timestamp_ms=payload.get("timestamp"),
            )
            return

        if event_type == "last_trade_price":
            asset_id = str(payload.get("asset_id") or "").strip()
            if not asset_id:
                return
            with self._lock:
                snapshot = self._quotes.get(asset_id) or QuoteSnapshot(asset_id=asset_id)
                snapshot.last_trade_price = self._to_float(payload.get("price"))
                snapshot.quote_updated_monotonic = time.monotonic()
                snapshot.timestamp_ms = self._to_int(payload.get("timestamp"))
                self._quotes[asset_id] = snapshot
            return

        if event_type in {"new_market", "market_resolved", "tick_size_change"}:
            logger.debug(f"Realtime market event: {event_type}")

    def _update_book(self, payload: dict) -> None:
        asset_id = str(payload.get("asset_id") or "").strip()
        if not asset_id:
            return

        bids = payload.get("bids") or []
        asks = payload.get("asks") or []
        best_bid = self._best_price(bids, choose_max=True)
        best_ask = self._best_price(asks, choose_max=False)
        now = time.monotonic()

        with self._lock:
            snapshot = self._quotes.get(asset_id) or QuoteSnapshot(asset_id=asset_id)
            snapshot.bids = [dict(level) for level in bids]
            snapshot.asks = [dict(level) for level in asks]
            snapshot.best_bid = best_bid
            snapshot.best_ask = best_ask
            snapshot.spread = (
                round(best_ask - best_bid, 6)
                if best_ask is not None and best_bid is not None
                else snapshot.spread
            )
            snapshot.quote_updated_monotonic = now
            snapshot.book_updated_monotonic = now
            snapshot.timestamp_ms = self._to_int(payload.get("timestamp"))
            self._quotes[asset_id] = snapshot

    def _update_quote(
        self,
        asset_id,
        best_bid,
        best_ask,
        spread,
        timestamp_ms,
    ) -> None:
        asset_id = str(asset_id or "").strip()
        if not asset_id:
            return

        now = time.monotonic()
        with self._lock:
            snapshot = self._quotes.get(asset_id) or QuoteSnapshot(asset_id=asset_id)
            bid_value = self._to_float(best_bid)
            ask_value = self._to_float(best_ask)
            if bid_value is not None:
                snapshot.best_bid = bid_value
            if ask_value is not None:
                snapshot.best_ask = ask_value
            spread_value = self._to_float(spread)
            if spread_value is not None:
                snapshot.spread = spread_value
            snapshot.quote_updated_monotonic = now
            snapshot.timestamp_ms = self._to_int(timestamp_ms)
            self._quotes[asset_id] = snapshot

    def _flush_subscription_changes(self, ws_app) -> None:
        desired = self._get_desired_assets()
        with self._lock:
            subscribed = set(self._subscribed_assets)

        if not subscribed and desired:
            sent = self._send_asset_batches(
                ws_app,
                payload_builder=lambda batch: {
                    "assets_ids": batch,
                    "type": "market",
                    "custom_feature_enabled": True,
                },
                asset_ids=sorted(desired),
            )
            if sent:
                with self._lock:
                    self._subscribed_assets = set(desired)
                self._subscription_dirty.clear()
            return

        to_subscribe = sorted(desired - subscribed)
        to_unsubscribe = sorted(subscribed - desired)
        subscribe_sent = True
        unsubscribe_sent = True

        if to_subscribe:
            subscribe_sent = self._send_asset_batches(
                ws_app,
                payload_builder=lambda batch: {
                    "assets_ids": batch,
                    "operation": "subscribe",
                    "custom_feature_enabled": True,
                },
                asset_ids=to_subscribe,
            )
            if subscribe_sent:
                with self._lock:
                    self._subscribed_assets.update(to_subscribe)

        if to_unsubscribe:
            unsubscribe_sent = self._send_asset_batches(
                ws_app,
                payload_builder=lambda batch: {
                    "assets_ids": batch,
                    "operation": "unsubscribe",
                },
                asset_ids=to_unsubscribe,
            )
            if unsubscribe_sent:
                with self._lock:
                    for asset_id in to_unsubscribe:
                        self._subscribed_assets.discard(asset_id)

        if subscribe_sent and unsubscribe_sent:
            self._subscription_dirty.clear()

    def _send_asset_batches(self, ws_app, payload_builder, asset_ids: list[str]) -> bool:
        if not asset_ids:
            return True
        chunk_size = 200
        for i in range(0, len(asset_ids), chunk_size):
            payload = payload_builder(asset_ids[i:i + chunk_size])
            if not self._safe_send_json(ws_app, payload):
                return False
        return True

    def _safe_send_json(self, ws_app, payload: dict) -> bool:
        return self._safe_send_text(ws_app, json.dumps(payload))

    def _safe_send_text(self, ws_app, payload: str) -> bool:
        try:
            with self._send_lock:
                ws_app.send(payload)
            return True
        except Exception as e:
            if not self._stop_event.is_set():
                logger.warning(f"Realtime market feed send failed: {e}")
            return False

    def _get_desired_assets(self) -> set[str]:
        with self._lock:
            return set(self._desired_assets)

    @staticmethod
    def _to_float(value) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _to_int(value) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    @classmethod
    def _best_price(cls, levels: list, choose_max: bool) -> Optional[float]:
        prices = [
            cls._to_float(level.get("price"))
            for level in levels
            if isinstance(level, dict)
        ]
        prices = [price for price in prices if price is not None]
        if not prices:
            return None
        return max(prices) if choose_max else min(prices)


_shared_feed = RealtimeMarketFeed()


def get_shared_feed() -> RealtimeMarketFeed:
    return _shared_feed
