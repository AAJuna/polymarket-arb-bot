"""
RTDS WebSocket client for real-time BTC price from Polymarket.

Connects to wss://ws-live-data.polymarket.com and subscribes to
both Binance and Chainlink BTC/USD price feeds. Maintains a rolling
buffer of (timestamp, price) tuples for volatility calculation.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from btc import config_btc as cfg
from logger_setup import get_logger

logger = get_logger(__name__)


@dataclass
class PriceTick:
    timestamp: float  # unix seconds
    price: float
    source: str  # "binance" or "chainlink"


class RtdsFeed:
    """Background WebSocket client for BTC price via Polymarket RTDS."""

    def __init__(self, max_history_seconds: int = 0) -> None:
        if max_history_seconds <= 0:
            max_history_seconds = cfg.VOLATILITY_LOOKBACK_SEC + 300
        self._max_history = max_history_seconds

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._connected = False
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._ping_thread: Optional[threading.Thread] = None
        self._websocket_module = None

        # Latest prices
        self._binance_price: Optional[float] = None
        self._chainlink_price: Optional[float] = None
        self._last_update_monotonic: float = 0.0

        # Rolling price history for volatility
        self._price_history: deque[PriceTick] = deque()

        # Stats
        self._message_count = 0
        self._reconnect_count = 0

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def get_btc_price(self) -> Optional[float]:
        """Return latest BTC price. Prefers Binance (faster), falls back to Chainlink."""
        with self._lock:
            if self._binance_price is not None:
                return self._binance_price
            return self._chainlink_price

    def get_chainlink_price(self) -> Optional[float]:
        """Return latest Chainlink BTC/USD price (resolution source)."""
        with self._lock:
            return self._chainlink_price

    def get_price_history(self, seconds: int = 0) -> list[tuple[float, float]]:
        """Return list of (timestamp, price) tuples within the last N seconds."""
        if seconds <= 0:
            seconds = self._max_history
        cutoff = time.time() - seconds
        with self._lock:
            return [
                (t.timestamp, t.price)
                for t in self._price_history
                if t.timestamp >= cutoff
            ]

    def last_update_age(self) -> Optional[float]:
        """Seconds since last price update, or None if never received."""
        with self._lock:
            if self._last_update_monotonic == 0.0:
                return None
            return time.monotonic() - self._last_update_monotonic

    def start(self) -> None:
        """Start the background WebSocket thread."""
        try:
            import websocket  # type: ignore
            self._websocket_module = websocket
        except ImportError:
            logger.error("websocket-client not installed -- RTDS feed disabled")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="rtds-feed", daemon=True
        )
        self._thread.start()
        logger.info(f"RTDS feed started -> {cfg.RTDS_WS_URL}")

    def stop(self) -> None:
        """Gracefully stop the WebSocket."""
        self._stop_event.set()
        ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("RTDS feed stopped")

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        backoff = cfg.RTDS_RECONNECT_BASE
        websocket = self._websocket_module
        if websocket is None:
            return

        while not self._stop_event.is_set():
            try:
                ws_app = websocket.WebSocketApp(
                    cfg.RTDS_WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws = ws_app
                ws_app.run_forever(ping_interval=0)  # we handle pings ourselves
            except Exception as e:
                logger.warning(f"RTDS WebSocket error: {e}")

            with self._lock:
                self._connected = False

            if self._stop_event.is_set():
                break

            self._reconnect_count += 1
            logger.info(f"RTDS reconnecting in {backoff:.1f}s (attempt {self._reconnect_count})")
            self._stop_event.wait(backoff)
            backoff = min(backoff * 2, cfg.RTDS_RECONNECT_MAX)

    def _on_open(self, ws) -> None:
        with self._lock:
            self._connected = True
        logger.info("RTDS WebSocket connected")

        # Subscribe to Binance BTC price
        sub_binance = json.dumps({
            "action": "subscribe",
            "subscriptions": [{
                "topic": "crypto_prices",
                "type": "update",
                "filters": json.dumps({"symbol": "btcusdt"}),
            }],
        })
        ws.send(sub_binance)

        # Subscribe to Chainlink BTC/USD (resolution source)
        sub_chainlink = json.dumps({
            "action": "subscribe",
            "subscriptions": [{
                "topic": "crypto_prices_chainlink",
                "type": "*",
                "filters": json.dumps({"symbol": "btc/usd"}),
            }],
        })
        ws.send(sub_chainlink)

        logger.info("RTDS subscribed to crypto_prices (btcusdt) + crypto_prices_chainlink (btc/usd)")

        # Start ping thread
        if self._ping_thread is None or not self._ping_thread.is_alive():
            self._ping_thread = threading.Thread(
                target=self._ping_loop, name="rtds-ping", daemon=True
            )
            self._ping_thread.start()

    def _on_message(self, ws, raw: str) -> None:
        if not raw or raw == "PONG":
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        topic = msg.get("topic", "")
        payload = msg.get("payload")
        if payload is None:
            return

        if topic == "crypto_prices":
            source = "binance"
        elif topic == "crypto_prices_chainlink":
            source = "chainlink"
        else:
            return

        # Handle both formats:
        #   {"payload": {"data": [{"timestamp": ms, "value": price}, ...]}}
        #   {"payload": {"value": price, "timestamp": ms}}
        ticks_raw = payload.get("data")
        if ticks_raw is None:
            # Single-tick format
            ticks_raw = [payload]

        for tick_data in ticks_raw:
            try:
                price = float(tick_data.get("value", 0))
            except (ValueError, TypeError):
                continue
            if price <= 0:
                continue

            # Use local time for history (server timestamps may differ)
            ts = time.time()

            if source == "binance":
                with self._lock:
                    self._binance_price = price
            else:
                with self._lock:
                    self._chainlink_price = price

            tick = PriceTick(timestamp=ts, price=price, source=source)
            with self._lock:
                self._price_history.append(tick)
                self._last_update_monotonic = time.monotonic()
                self._message_count += 1

        # Trim old entries
        with self._lock:
            cutoff = time.time() - self._max_history
            while self._price_history and self._price_history[0].timestamp < cutoff:
                self._price_history.popleft()

    def _on_error(self, ws, error) -> None:
        logger.warning(f"RTDS WebSocket error: {error}")

    def _on_close(self, ws, close_status_code=None, close_msg=None) -> None:
        with self._lock:
            self._connected = False
        logger.info(f"RTDS WebSocket closed (code={close_status_code})")

    def _ping_loop(self) -> None:
        """Send PING every N seconds to keep the connection alive."""
        while not self._stop_event.is_set():
            self._stop_event.wait(cfg.RTDS_PING_INTERVAL)
            ws = self._ws
            if ws and self._connected:
                try:
                    ws.send("PING")
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def log_status(self) -> None:
        age = self.last_update_age()
        age_label = f"{age:.0f}s ago" if age is not None else "n/a"
        with self._lock:
            bp = self._binance_price
            cp = self._chainlink_price
            msgs = self._message_count
            hist = len(self._price_history)
        logger.info(
            f"RTDS: {'OK' if self.is_connected else 'DOWN'}  "
            f"binance=${bp:,.2f}  chainlink=${cp:,.2f}  "
            f"msgs={msgs}  history={hist}  last={age_label}"
            if bp and cp
            else f"RTDS: {'OK' if self.is_connected else 'DOWN'}  "
            f"msgs={msgs}  history={hist}  last={age_label}"
        )
