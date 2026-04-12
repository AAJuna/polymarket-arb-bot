"""
BTC 5-minute prediction bot for Polymarket.

State machine:
  IDLE -> WAITING -> OBSERVING -> TRADING -> RESOLVING -> IDLE

Run: python -m btc.main_btc
"""

from __future__ import annotations

import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from btc import config_btc as cfg
from btc.btc_scanner import BtcMarket, BtcScanner
from btc.rtds_feed import RtdsFeed
from btc.signal_engine import BtcSignal, SignalEngine
from logger_setup import get_logger, setup_logging

logger = get_logger(__name__)


class State(Enum):
    IDLE = auto()
    WAITING = auto()
    OBSERVING = auto()
    TRADING = auto()
    RESOLVING = auto()


@dataclass
class BtcOpportunity:
    """Adapter: makes a BtcSignal look like an Opportunity for executor/portfolio."""

    type: str
    market_id: str
    condition_id: str
    token_id: str
    side: str
    price: float
    edge_pct: float
    confidence_source: str
    yes_price: float
    no_price: float
    question: str
    end_date: datetime
    slug: str = ""
    event_slug: str = ""
    market_slug: str = ""
    market_url: str = ""
    value_score: float = 0.0
    raw_data: dict = field(default_factory=dict)


def _build_opportunity(signal: BtcSignal, market: BtcMarket) -> BtcOpportunity:
    """Convert a BtcSignal + BtcMarket into an executor-compatible opportunity."""
    if signal.side == "UP":
        token_id = market.up_token_id
    else:
        token_id = market.down_token_id

    return BtcOpportunity(
        type="btc_5min",
        market_id=market.market_id,
        condition_id=market.condition_id,
        token_id=token_id,
        side="YES",  # always buying the chosen side
        price=signal.market_price,
        edge_pct=signal.edge_pct,
        confidence_source="btc_signal",
        yes_price=market.up_price,
        no_price=market.down_price,
        question=market.question,
        end_date=market.window_end,
        raw_data={
            "order_price_min_tick_size": market.tick_size,
            "neg_risk": market.neg_risk,
        },
    )


def run() -> None:
    setup_logging(cfg.LOG_LEVEL)

    # Banner
    logger.info("=" * 56)
    logger.info("  BTC 5-Minute Prediction Bot")
    logger.info(f"  Paper: {cfg.PAPER_TRADING}  Edge: {cfg.MIN_EDGE_PCT}%  "
                f"Conf: {cfg.MIN_CONFIDENCE}")
    logger.info("=" * 56)

    # Validate config
    for level, issue in cfg.validate():
        getattr(logger, level, logger.warning)(f"Config: {issue}")

    # Initialize components
    rtds = RtdsFeed()
    scanner = BtcScanner()
    engine = SignalEngine(rtds)

    # Import shared infrastructure
    # Patch portfolio data dir for isolation BEFORE importing
    import portfolio as portfolio_module
    data_dir = Path("data") / cfg.PORTFOLIO_SUBDIR
    data_dir.mkdir(parents=True, exist_ok=True)
    portfolio_module.DATA_DIR = data_dir
    portfolio_module.PORTFOLIO_FILE = data_dir / "portfolio.json"
    portfolio_module.PORTFOLIO_BACKUP_FILE = data_dir / "portfolio.json.bak"
    portfolio_module.TRADE_LEDGER_FILE = data_dir / "trade_ledger.jsonl"
    portfolio_module.STRATEGY_REPORT_FILE = data_dir / "strategy_expectancy.json"

    # Patch shared config so Executor/RiskManager use BTC settings
    import config as shared_config
    shared_config.PAPER_TRADING = cfg.PAPER_TRADING

    from portfolio import Portfolio
    from executor import Executor
    from risk_manager import RiskManager

    port = Portfolio(starting_bankroll=cfg.MAX_POSITION_SIZE)
    port.load()

    executor = Executor()
    risk_mgr = RiskManager(port)

    # Start feeds
    rtds.start()

    # Wait a moment for RTDS to connect and get initial price
    logger.info("Waiting for RTDS price feed...")
    for _ in range(30):
        if rtds.get_btc_price() is not None:
            break
        time.sleep(1.0)

    btc_price = rtds.get_btc_price()
    if btc_price:
        logger.info(f"RTDS ready: BTC=${btc_price:,.2f}")
    else:
        logger.warning("RTDS no price after 30s -- continuing anyway")

    # Graceful shutdown
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received")
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # State machine
    state = State.IDLE
    current_market: Optional[BtcMarket] = None
    current_position_id: Optional[str] = None
    cycle = 0
    last_status_log = 0.0

    while running:
        cycle += 1
        cycle_start = time.monotonic()

        try:
            # ============================================================
            # IDLE: Find next market
            # ============================================================
            if state == State.IDLE:
                market = scanner.get_tradeable_window()
                if market:
                    current_market = market
                    state = State.WAITING
                    logger.info(
                        f"Found market: {market.question[:60]}  "
                        f"start={market.window_start.strftime('%H:%M:%S')}  "
                        f"end={market.window_end.strftime('%H:%M:%S')}"
                    )
                _sleep(cfg.POLL_INTERVAL_IDLE, running)

            # ============================================================
            # WAITING: Wait for window to open
            # ============================================================
            elif state == State.WAITING:
                now = datetime.now(timezone.utc)
                if now >= current_market.window_start:
                    # Capture strike price
                    strike = rtds.get_btc_price()
                    if strike:
                        duration = (
                            current_market.window_end - current_market.window_start
                        ).total_seconds()
                        engine.set_window(strike, current_market.window_start, duration)
                        state = State.OBSERVING
                        logger.info(
                            f"Window OPEN: strike=${strike:,.2f}  "
                            f"Up={current_market.up_price:.2f}  "
                            f"Down={current_market.down_price:.2f}"
                        )
                    else:
                        logger.warning("No BTC price at window start -- skipping")
                        engine.reset()
                        current_market = None
                        state = State.IDLE
                _sleep(0.5, running)

            # ============================================================
            # OBSERVING: Compute signal, enter if edge found
            # ============================================================
            elif state == State.OBSERVING:
                now = datetime.now(timezone.utc)
                time_remaining = (current_market.window_end - now).total_seconds()

                # Check if we've passed the entry deadline or window ended
                if time_remaining <= cfg.EXIT_BEFORE_END_SEC:
                    logger.info("Entry deadline passed -- skipping to next window")
                    engine.reset()
                    current_market = None
                    state = State.IDLE
                    continue

                # Refresh market prices from scanner
                refreshed = scanner.get_current_window()
                if refreshed and refreshed.condition_id == current_market.condition_id:
                    current_market = refreshed

                sig = engine.get_signal(
                    current_market.up_price, current_market.down_price
                )

                if sig and sig.edge_pct >= cfg.MIN_EDGE_PCT and sig.confidence >= cfg.MIN_CONFIDENCE:
                    # Risk check
                    opp = _build_opportunity(sig, current_market)
                    size_dollars = risk_mgr.get_position_size(
                        adjusted_bet_pct=cfg.BET_SIZE_PCT,
                        edge_pct=sig.edge_pct,
                        price=sig.market_price,
                        ai_confidence=sig.confidence,
                    )
                    size_dollars = min(size_dollars, cfg.MAX_POSITION_SIZE)
                    can_trade, block_reason = risk_mgr.can_trade(opp, size_dollars)

                    if can_trade:

                        result = executor.place_order(opp, size_dollars)
                        if result:
                            pos = port.record_trade(opp, result)
                            current_position_id = pos.position_id
                            state = State.TRADING
                            logger.info(
                                f"TRADE: {sig.side} @ {sig.market_price:.2f}  "
                                f"edge={sig.edge_pct:.1f}%  "
                                f"conf={sig.confidence:.2f}  "
                                f"BTC=${sig.btc_price:,.2f} vs strike=${sig.strike_price:,.2f}"
                            )
                    else:
                        logger.debug(f"Risk blocked: {block_reason}")
                elif sig:
                    if cycle % 30 == 0:  # log every ~30 seconds
                        logger.debug(
                            f"Signal: {sig.side}  edge={sig.edge_pct:.1f}%  "
                            f"conf={sig.confidence:.2f}  "
                            f"BTC=${sig.btc_price:,.2f}"
                        )

                _sleep(cfg.POLL_INTERVAL_ACTIVE, running)

            # ============================================================
            # TRADING: Monitor position, wait for window end
            # ============================================================
            elif state == State.TRADING:
                now = datetime.now(timezone.utc)
                time_remaining = (current_market.window_end - now).total_seconds()

                btc_price = rtds.get_btc_price()
                if btc_price and engine.is_ready:
                    strike = engine._strike_price
                    direction = "UP" if btc_price >= strike else "DOWN"
                    if cycle % 10 == 0:
                        logger.info(
                            f"Position open | BTC=${btc_price:,.2f}  "
                            f"strike=${strike:,.2f}  direction={direction}  "
                            f"remaining={time_remaining:.0f}s"
                        )

                if time_remaining <= 0:
                    state = State.RESOLVING
                    logger.info("Window ended -- waiting for resolution")

                _sleep(cfg.POLL_INTERVAL_ACTIVE, running)

            # ============================================================
            # RESOLVING: Wait for market resolution
            # ============================================================
            elif state == State.RESOLVING:
                # Check if market resolved via portfolio resolution check
                port.check_resolutions()
                has_open = current_position_id in port.state.open_positions
                if not has_open or current_position_id is None:
                    port.log_status()
                    engine.reset()
                    current_market = None
                    current_position_id = None
                    state = State.IDLE
                    logger.info("Resolution complete -- moving to next window")
                else:
                    _sleep(cfg.POLL_INTERVAL_IDLE, running)

            # Periodic status log
            if time.monotonic() - last_status_log > 60:
                rtds.log_status()
                port.log_status()
                last_status_log = time.monotonic()

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Cycle {cycle} error: {e}", exc_info=True)
            _sleep(cfg.POLL_INTERVAL_IDLE, running)

    # Shutdown
    logger.info("Shutting down BTC bot...")
    rtds.stop()
    port.save()
    logger.info("BTC bot stopped.")


def _sleep(seconds: float, running: bool) -> None:
    """Sleep in small increments so shutdown is responsive."""
    end = time.monotonic() + seconds
    while running and time.monotonic() < end:
        time.sleep(min(0.25, end - time.monotonic()))


if __name__ == "__main__":
    run()
