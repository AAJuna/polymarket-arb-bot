"""
Main Orchestrator — startup validation, main trading loop, graceful shutdown.

Usage:
    python main.py

Environment:
    Copy .env.example → .env and fill in your credentials before running.
    Set PAPER_TRADING=true (default) for safe testing without real orders.
"""

import argparse
import signal
import sys
import time

import config
import scanner
import arbitrage
from ai_analyzer import AIAnalyzer
from compounder import Compounder
from executor import Executor
from logger_setup import setup_logging, get_logger
from maintenance import reset_runtime_state
from portfolio import Portfolio
from risk_manager import RiskManager
from tabulate import tabulate

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def startup_checks(
    port: Portfolio,
    exec_: Executor,
    ai: AIAnalyzer,
) -> None:
    """Validate connectivity and credentials before entering the main loop."""
    logger.info("=" * 60)
    logger.info("  Polymarket Sports Arbitrage Bot — Starting Up")
    logger.info("=" * 60)

    issues = config.validate()
    for issue in issues:
        logger.warning(f"Config: {issue}")

    if not config.PRIVATE_KEY and not config.PAPER_TRADING:
        logger.critical("No POLYMARKET_PRIVATE_KEY and PAPER_TRADING=false — cannot trade")
        sys.exit(1)

    # Test Gamma API
    logger.info("Testing Gamma API connection...")
    test_markets = scanner.scan_sports_markets()
    if not test_markets:
        logger.critical("Gamma API returned 0 sports markets — check connection")
        sys.exit(1)
    logger.info(f"  Gamma API OK — {len(test_markets)} sports markets found")

    # Test Claude API
    if config.ANTHROPIC_API_KEY:
        logger.info("Testing Claude API connection...")
        result = ai.analyze_test()
        if result:
            logger.info(f"  Claude API OK — model={config.AI_MODEL}")
        else:
            logger.warning("  Claude API test failed — AI validation will be skipped")
    else:
        logger.warning("  ANTHROPIC_API_KEY not set — AI validation disabled")

    # Load portfolio
    port.load()

    # Sync bankroll (paper trading starts with a seed if empty)
    if config.PAPER_TRADING and port.state.current_bankroll == 0:
        seed = config.INITIAL_BET_SIZE * 50  # $100 paper money by default
        port.state.starting_bankroll = seed
        port.state.current_bankroll = seed
        port.state.peak_bankroll = seed
        port.state.day_start_bankroll = seed
        logger.info(f"  Paper trading seed bankroll: ${seed:.2f}")
    elif not config.PAPER_TRADING:
        balance = exec_.get_usdc_balance()
        if balance is not None:
            if port.state.starting_bankroll == 0:
                port.state.starting_bankroll = balance
                port.state.peak_bankroll = balance
                port.state.day_start_bankroll = balance
            port.sync_bankroll(balance)
            logger.info(f"  CLOB USDC balance: ${balance:.2f}")

    _print_startup_summary(port)
    logger.info("Startup complete. Entering main loop.\n")


def _print_startup_summary(port: Portfolio) -> None:
    s = port.state
    rows = [
        ["Bankroll", f"${s.current_bankroll:.2f}"],
        ["Peak bankroll", f"${s.peak_bankroll:.2f}"],
        ["Open positions", len(s.open_positions)],
        ["Total trades", s.total_trades],
        ["Mode", "PAPER" if config.PAPER_TRADING else "LIVE"],
        ["AI model", config.AI_MODEL],
        ["Min edge", f"{config.MIN_EDGE_PCT:.1f}%"],
        ["Min AI confidence", f"{config.MIN_AI_CONFIDENCE:.0%}"],
        ["Bet size", f"{config.BET_SIZE_PCT:.1f}% of bankroll"],
        ["Max bet", f"${config.MAX_BET_SIZE:.2f}"],
        ["Poll interval", f"{config.POLL_INTERVAL}s"],
    ]
    print(tabulate(rows, tablefmt="simple"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket sports arbitrage bot")
    parser.add_argument(
        "--fresh-start",
        action="store_true",
        help="Delete persisted portfolio, AI stats, and optionally logs before startup",
    )
    parser.add_argument(
        "--keep-logs",
        action="store_true",
        help="With --fresh-start, preserve logs/ while clearing saved bot state",
    )
    return parser.parse_args(argv)


def _log_reset_summary(summary: dict) -> None:
    removed = len(summary.get("removed") or [])
    errors = summary.get("errors") or []
    logger.warning(
        f"Fresh start reset complete — removed {removed} file(s), "
        f"clear_logs={summary.get('clear_logs', False)}"
    )
    for err in errors:
        logger.warning(f"Fresh start reset warning: {err}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    ai = AIAnalyzer()
    port = Portfolio(starting_bankroll=0.0)
    exec_ = Executor()
    comp = Compounder()
    risk = RiskManager(port)

    startup_checks(port, exec_, ai)

    running = True
    _shutdown_count = [0]

    def _shutdown(signum, frame):
        nonlocal running
        _shutdown_count[0] += 1
        if _shutdown_count[0] == 1:
            logger.info("Ctrl+C — finishing current cycle then exiting (press again to force quit)...")
            running = False
        else:
            logger.warning("Force quit!")
            sys.exit(0)

    def _reload_config(signum, frame):
        """SIGUSR1 — reload .env without restarting the bot."""
        try:
            from dotenv import load_dotenv
            import importlib
            load_dotenv(override=True)
            importlib.reload(config)
            # Update AI confidence threshold live
            logger.info(
                f"Config reloaded — "
                f"MIN_AI_CONFIDENCE={config.MIN_AI_CONFIDENCE} "
                f"MIN_EDGE_PCT={config.MIN_EDGE_PCT} "
                f"BET_SIZE_PCT={config.BET_SIZE_PCT} "
                f"PAPER_TRADING={config.PAPER_TRADING}"
            )
        except Exception as e:
            logger.error(f"Config reload failed: {e}")

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, _reload_config)

    cycle = 0
    bankroll_sync_counter = 0

    while running:
        cycle_start = time.monotonic()
        cycle += 1

        try:
            # Check if trading is globally blocked before doing any API calls
            trading_blocked, block_reason = risk.is_globally_blocked()

            if trading_blocked:
                open_before = len(port.state.open_positions)
                port.check_resolutions()
                open_after = len(port.state.open_positions)
                closed_count = open_before - open_after

                if closed_count > 0:
                    # A position resolved — re-evaluate, may allow new entries
                    trading_blocked, block_reason = risk.is_globally_blocked()
                    logger.info(
                        f"━━ CYCLE {cycle:>4} ━━ {closed_count} position(s) resolved — "
                        f"{'resuming trading' if not trading_blocked else f'still blocked ({block_reason})'} | "
                        f"bankroll=${port.state.current_bankroll:.2f} | open={open_after}"
                    )

                if trading_blocked:
                    logger.info(
                        f"━━ CYCLE {cycle:>4} ━━ IDLE ({block_reason}) | "
                        f"bankroll=${port.state.current_bankroll:.2f} | "
                        f"open={len(port.state.open_positions)}"
                    )

            if not trading_blocked:
                # 1. Scan markets
                markets = scanner.scan_sports_markets()

                # 2. Detect arbitrage opportunities
                opportunities = arbitrage.find_opportunities(markets)

                # 3. Pre-filter — skip markets already open, apply edge threshold
                #    Cap at top 5 by edge score to limit AI token spend
                with port._lock:
                    open_market_ids = {
                        p.get("market_id") for p in port.state.open_positions.values()
                    }
                filtered = sorted(
                    [
                        o for o in opportunities
                        if o.edge_pct >= config.MIN_EDGE_PCT
                        and o.market_id not in open_market_ids
                    ],
                    key=lambda o: o.edge_pct,
                    reverse=True,
                )[:5]  # Only top 5 sent to AI — saves tokens

                verified_candidates = []
                for opp in filtered:
                    is_open, _, reason = scanner.verify_market_open(opp.condition_id)
                    if not is_open:
                        logger.info(
                            f"  skipped closed market {opp.market_id} "
                            f"({reason}) | {opp.question[:50]}"
                        )
                        continue
                    verified_candidates.append(opp)
                filtered = verified_candidates

                logger.info(
                    f"━━ CYCLE {cycle:>4} ━━ "
                    f"markets={len(markets)} | "
                    f"opps={len(opportunities)} | "
                    f"queued={len(filtered)} | "
                    f"bankroll=${port.state.current_bankroll:.2f} | "
                    f"open={len(port.state.open_positions)}"
                )

                # 4. AI validation
                validated = []
                for opp in filtered:
                    analysis = ai.analyze(opp)
                    if analysis is None:
                        if not config.ANTHROPIC_API_KEY:
                            from ai_analyzer import AIAnalysis
                            analysis = AIAnalysis(
                                predicted_probability=opp.yes_price,
                                confidence=1.0,
                                reasoning="AI disabled",
                                edge_detected=True,
                                recommended_side=opp.side,
                                risk_factors=[],
                            )
                        else:
                            continue

                    if analysis.is_valid and analysis.recommended_side == opp.side:
                        validated.append((opp, analysis))
                    elif analysis.is_valid:
                        logger.info(
                            f"  skipped AI side mismatch for {opp.market_id} "
                            f"(opp={opp.side}, ai={analysis.recommended_side})"
                        )

                if validated:
                    logger.info(f"  ✓ {len(validated)} passed AI validation → executing")
                else:
                    logger.info(f"  ✗ 0/{len(filtered)} passed AI validation")

                # 5. Execute
                trades_placed = 0
                for opp, analysis in validated:
                    size = risk.get_position_size(comp.current_bet_pct)
                    allowed, reason = risk.can_trade(opp, size)

                    if not allowed:
                        logger.info(f"  blocked ({reason})")
                        continue

                    is_open, _, status_reason = scanner.verify_market_open(opp.condition_id)
                    if not is_open:
                        logger.info(
                            f"  blocked (market_{status_reason}) | {opp.question[:50]}"
                        )
                        continue

                    result = exec_.place_order(opp, size)
                    if result and result.get("success"):
                        port.record_trade(opp, result, analysis)
                        comp.update(port.state)
                        trades_placed += 1
                        open_market_ids.add(opp.market_id)

                if trades_placed:
                    logger.info(f"  → {trades_placed} trade(s) placed this cycle")

                # 6. Check resolved markets
                port.check_resolutions()

            # 7. Periodic bankroll sync (every ~60 seconds)
            bankroll_sync_counter += 1
            if bankroll_sync_counter >= (60 // config.POLL_INTERVAL):
                bankroll_sync_counter = 0
                balance = exec_.get_usdc_balance()
                if balance is not None:
                    port.sync_bankroll(balance)

            # 8. Status log (every ~60 seconds)
            if cycle % max(1, 60 // config.POLL_INTERVAL) == 0:
                port.log_status()
                ai.log_usage()

            # 9. Periodic save
            port.maybe_save()

        except Exception as e:
            logger.error(f"Main loop error (cycle {cycle}): {e}", exc_info=True)

        # Sleep for remainder of poll interval
        elapsed = time.monotonic() - cycle_start
        sleep_time = max(0.0, config.POLL_INTERVAL - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)

    # Graceful shutdown
    logger.info("Shutting down...")
    port.save()
    exec_.cancel_all()
    port.log_status()
    logger.info("Bot stopped cleanly.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    reset_summary = None
    should_reset = args.fresh_start or config.RESET_STATE_ON_START
    clear_logs = config.RESET_LOGS_ON_START and not args.keep_logs
    if should_reset:
        reset_summary = reset_runtime_state(clear_logs=clear_logs)

    setup_logging(config.LOG_LEVEL)

    if reset_summary is not None:
        _log_reset_summary(reset_summary)

    run()


if __name__ == "__main__":
    main()
