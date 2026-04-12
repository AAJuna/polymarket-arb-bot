"""
BTC 5-minute prediction bot for Polymarket.

State machine:
  IDLE -> WAITING -> OBSERVING -> TRADING -> RESOLVING -> IDLE

Run: python -m btc.main_btc
"""

from __future__ import annotations

import json
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
    COLLECTING = auto()  # first 60s: gather price data
    ANALYZING = auto()   # AI analyzes, waits for entry time
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


def _market_url(market: BtcMarket) -> str:
    """Build Polymarket URL from market's window start timestamp."""
    ts = int(market.window_start.timestamp())
    return f"https://polymarket.com/event/btc-updown-5m-{ts}"


def _market_id_slug(market: BtcMarket) -> str:
    """Extract the unix timestamp ID from market window."""
    return str(int(market.window_start.timestamp()))


def _build_opportunity(
    side: str,
    confidence: float,
    market: BtcMarket,
    strategy: str = "",
) -> BtcOpportunity:
    """Build an executor-compatible opportunity from AI decision."""
    if side == "UP":
        token_id = market.up_token_id
        opp_side = "YES"   # Up = outcome[0] → YES → outcome_prices[0]
        price = market.up_price
    else:
        token_id = market.down_token_id
        opp_side = "NO"    # Down = outcome[1] → NO → outcome_prices[1]
        price = market.down_price

    url = _market_url(market)
    mid = _market_id_slug(market)

    return BtcOpportunity(
        type="btc_5min",
        market_id=market.market_id,
        condition_id=market.condition_id,
        token_id=token_id,
        side=opp_side,
        price=price,
        edge_pct=0.0,
        confidence_source=strategy or "ai_haiku",
        yes_price=market.up_price,
        no_price=market.down_price,
        question=market.question,
        end_date=market.window_end,
        slug=f"btc-updown-5m-{mid}",
        market_url=url,
        raw_data={
            "order_price_min_tick_size": market.tick_size,
            "neg_risk": market.neg_risk,
        },
    )


SIGNAL_STATUS_FILE = Path("data/btc/signal_status.json")


def _write_signal_status(
    state: State,
    rtds: "RtdsFeed",
    engine: "SignalEngine",
    market: Optional[BtcMarket] = None,
    sig: Optional[BtcSignal] = None,
    position_id: Optional[str] = None,
) -> None:
    """Write current signal engine state to JSON for dashboard consumption."""
    now = datetime.now(timezone.utc)
    btc_price = rtds.get_btc_price()

    status: dict = {
        "updated_at": now.isoformat(),
        "state": state.name,
        "btc_price": btc_price,
        "rtds_connected": rtds.is_connected,
        "rtds_msgs": rtds._message_count,
    }

    if market:
        time_remaining = max(0, (market.window_end - now).total_seconds())
        status["market"] = {
            "question": market.question,
            "window_start": market.window_start.isoformat(),
            "window_end": market.window_end.isoformat(),
            "time_remaining_sec": round(time_remaining),
            "up_price": market.up_price,
            "down_price": market.down_price,
        }

    if engine.is_ready:
        status["strike_price"] = engine._strike_price

    if sig:
        status["signal"] = {
            "side": sig.side,
            "model_probability": round(sig.model_probability, 4),
            "market_price": round(sig.market_price, 4),
            "edge_pct": round(sig.edge_pct, 2),
            "confidence": round(sig.confidence, 3),
            "volatility": round(sig.volatility, 4),
            "time_remaining_sec": round(sig.time_remaining_sec),
            "statistical_prob": round(sig.statistical_prob, 4),
            "momentum_adj": round(sig.momentum_adj, 4),
            "orderflow_adj": round(sig.orderflow_adj, 4),
        }

    if position_id:
        status["position_id"] = position_id

    try:
        SIGNAL_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SIGNAL_STATUS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2)
        tmp.replace(SIGNAL_STATUS_FILE)
    except Exception:
        pass


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

    from btc.ai_analyzer import BtcAIAnalyzer
    ai = BtcAIAnalyzer()
    if ai.enabled:
        logger.info(f"AI analyzer ready (model={cfg.AI_MODEL})")
    else:
        logger.warning("AI analyzer disabled — no ANTHROPIC_API_KEY")

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
    ai_decision: Optional[object] = None  # BtcAIAnalysis
    skipped_condition_ids: set[str] = set()  # don't re-enter skipped windows
    cycle = 0
    last_status_log = 0.0
    last_resolution_check = 0.0

    while running:
        cycle += 1
        cycle_start = time.monotonic()

        try:
            # Check resolutions every 15s
            if port.state.open_positions and (cycle_start - last_resolution_check) > 15:
                port.check_resolutions()
                last_resolution_check = cycle_start

            # ============================================================
            # IDLE: Find next market
            # ============================================================
            if state == State.IDLE:
                market = scanner.get_tradeable_window()
                if market and market.condition_id in skipped_condition_ids:
                    market = scanner.get_next_window()
                if market and market.condition_id in skipped_condition_ids:
                    market = None
                if market:
                    current_market = market
                    ai_decision = None
                    url = _market_url(market)
                    state = State.WAITING
                    logger.info(
                        f"Found: {market.question[:50]}  "
                        f"start={market.window_start.strftime('%H:%M:%S')}  "
                        f"end={market.window_end.strftime('%H:%M:%S')}  "
                        f"{url}"
                    )
                _write_signal_status(state, rtds, engine, current_market)
                _sleep(cfg.POLL_INTERVAL_IDLE, running)

            # ============================================================
            # WAITING: Wait for window to open
            # ============================================================
            elif state == State.WAITING:
                now = datetime.now(timezone.utc)
                if now >= current_market.window_start:
                    strike = rtds.get_btc_price() or 0.0
                    duration = (
                        current_market.window_end - current_market.window_start
                    ).total_seconds()
                    engine.set_window(strike, current_market.window_start, duration)
                    state = State.COLLECTING
                    logger.info(
                        f"Window OPEN: BTC=${strike:,.2f}  "
                        f"Up={current_market.up_price:.2f}  "
                        f"Down={current_market.down_price:.2f}  "
                        f"| Collecting {cfg.AI_COLLECT_SEC:.0f}s of data..."
                    )
                _sleep(0.5, running)

            # ============================================================
            # COLLECTING: Gather price data for AI analysis (first 60s)
            # ============================================================
            elif state == State.COLLECTING:
                now = datetime.now(timezone.utc)
                elapsed = (now - current_market.window_start).total_seconds()

                _write_signal_status(state, rtds, engine, current_market)

                if elapsed >= cfg.AI_COLLECT_SEC:
                    # Send data to Haiku
                    btc_price = rtds.get_btc_price() or 0.0
                    price_history = rtds.get_price_history(seconds=int(cfg.AI_COLLECT_SEC))
                    url = _market_url(current_market)

                    logger.info(
                        f"Sending {len(price_history)} ticks to AI for analysis..."
                    )

                    if ai.enabled:
                        ai_decision = ai.analyze(
                            price_history=price_history,
                            up_price=current_market.up_price,
                            down_price=current_market.down_price,
                            btc_price=btc_price,
                            time_remaining_sec=(current_market.window_end - now).total_seconds(),
                            market_question=current_market.question,
                            market_url=url,
                        )
                    else:
                        # Fallback to momentum signal engine
                        sig = engine.get_signal(current_market.up_price, current_market.down_price)
                        if sig and sig.confidence >= cfg.MIN_CONFIDENCE:
                            from btc.ai_analyzer import BtcAIAnalysis
                            ai_decision = BtcAIAnalysis(
                                side=sig.side,
                                confidence=sig.confidence,
                                strategy="momentum_fallback",
                                reasoning=f"Momentum signal {sig.side} edge={sig.edge_pct:.1f}%",
                            )

                    if ai_decision and ai_decision.is_valid:
                        logger.info(
                            f"AI decision: {ai_decision.side}  "
                            f"conf={ai_decision.confidence:.0%}  "
                            f"strategy={ai_decision.strategy}  "
                            f"| Waiting for entry at t={cfg.AI_ENTRY_AT_SEC:.0f}s"
                        )
                        state = State.ANALYZING
                    else:
                        reason = ai_decision.reasoning if ai_decision else "no analysis"
                        logger.info(f"AI says SKIP: {reason}")
                        skipped_condition_ids.add(current_market.condition_id)
                        engine.reset()
                        current_market = None
                        ai_decision = None
                        scanner.invalidate_cache()
                        state = State.IDLE

                _sleep(cfg.POLL_INTERVAL_ACTIVE, running)

            # ============================================================
            # ANALYZING: AI decided, wait for entry time (minute 3)
            # ============================================================
            elif state == State.ANALYZING:
                now = datetime.now(timezone.utc)
                elapsed = (now - current_market.window_start).total_seconds()
                time_remaining = (current_market.window_end - now).total_seconds()

                _write_signal_status(state, rtds, engine, current_market)

                if time_remaining <= cfg.EXIT_BEFORE_END_SEC:
                    logger.info("Window ending — too late to enter")
                    engine.reset()
                    current_market = None
                    ai_decision = None
                    scanner.invalidate_cache()
                    state = State.IDLE
                    continue

                # Execute at entry time (minute 3 = 180s)
                if elapsed >= cfg.AI_ENTRY_AT_SEC:
                    opp = _build_opportunity(
                        side=ai_decision.side,
                        confidence=ai_decision.confidence,
                        market=current_market,
                        strategy=ai_decision.strategy,
                    )

                    size_dollars = risk_mgr.get_position_size(
                        adjusted_bet_pct=cfg.BET_SIZE_PCT,
                        edge_pct=0.0,
                        price=opp.price,
                        ai_confidence=ai_decision.confidence,
                    )
                    size_dollars = min(size_dollars, cfg.MAX_POSITION_SIZE)
                    can_trade, block_reason = risk_mgr.can_trade(opp, size_dollars)

                    if can_trade:
                        result = executor.place_order(opp, size_dollars)
                        if result:
                            pos = port.record_trade(opp, result, ai_decision)
                            current_position_id = pos.position_id
                            url = _market_url(current_market)
                            mid = _market_id_slug(current_market)
                            state = State.TRADING
                            logger.info(
                                f"ENTRY: {ai_decision.side} @ {opp.price:.2f}  "
                                f"conf={ai_decision.confidence:.0%}  "
                                f"strategy={ai_decision.strategy}  "
                                f"| ID={mid}  {url}"
                            )
                    else:
                        logger.info(f"Risk blocked: {block_reason}")
                        engine.reset()
                        current_market = None
                        ai_decision = None
                        scanner.invalidate_cache()
                        state = State.IDLE

                _sleep(cfg.POLL_INTERVAL_ACTIVE, running)

            # ============================================================
            # TRADING: Monitor position, wait for window end
            # ============================================================
            elif state == State.TRADING:
                now = datetime.now(timezone.utc)
                time_remaining = (current_market.window_end - now).total_seconds()

                btc_price = rtds.get_btc_price()
                if btc_price and engine.is_ready:
                    strike = engine._strike_price or 0
                    direction = "UP" if btc_price >= strike else "DOWN"
                    if cycle % 10 == 0:
                        logger.info(
                            f"Position open | BTC=${btc_price:,.2f}  "
                            f"direction={direction}  "
                            f"remaining={time_remaining:.0f}s"
                        )

                _write_signal_status(state, rtds, engine, current_market, position_id=current_position_id)

                if time_remaining <= 0:
                    state = State.RESOLVING
                    logger.info("Window ended — waiting for resolution")

                _sleep(cfg.POLL_INTERVAL_ACTIVE, running)

            # ============================================================
            # RESOLVING: Wait for market resolution
            # ============================================================
            elif state == State.RESOLVING:
                has_open = current_position_id in port.state.open_positions
                if not has_open or current_position_id is None:
                    port.log_status()
                    engine.reset()
                    current_market = None
                    current_position_id = None
                    ai_decision = None
                    scanner.invalidate_cache()
                    state = State.IDLE
                    logger.info("Resolution complete — next window")
                else:
                    if current_market:
                        overdue = (datetime.now(timezone.utc) - current_market.window_end).total_seconds()
                        if overdue > 90:
                            logger.info(f"Resolution pending {overdue:.0f}s — moving on")
                            engine.reset()
                            current_market = None
                            current_position_id = None
                            ai_decision = None
                            scanner.invalidate_cache()
                            state = State.IDLE
                            continue
                    _sleep(cfg.POLL_INTERVAL_IDLE, running)

            # Trim skipped set (keep only recent)
            if len(skipped_condition_ids) > 20:
                skipped_condition_ids = set(list(skipped_condition_ids)[-10:])

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
