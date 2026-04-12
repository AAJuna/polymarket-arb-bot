"""
Main Orchestrator — startup validation, main trading loop, graceful shutdown.

Usage:
    python main.py

Environment:
    Copy .env.example → .env and fill in your credentials before running.
    Set PAPER_TRADING=true (default) for safe testing without real orders.
"""

import argparse
import json
import signal
import sys
import time
from pathlib import Path

import config
import scanner
import arbitrage
from ai_analyzer import AIAnalyzer
from compounder import Compounder
from executor import Executor
from logger_setup import setup_logging, get_logger
from maintenance import reset_runtime_state
from portfolio import Portfolio
from realtime_feed import get_shared_feed
from risk_manager import RiskManager
from risk_events import RiskEventJournal
from shadow_tracker import ShadowTracker
from tabulate import tabulate

logger = get_logger(__name__)
EXECUTION_VERIFY_MAX_AGE_SECONDS = 5.0


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
        account_address = exec_.get_account_address()
        if account_address:
            logger.info(f"  Data API reconciliation address: {account_address[:10]}...")
            port.reconcile_live_account(account_address)

    # Persist a startup snapshot immediately so the dashboard has state to read.
    port.save()

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
        ["AI exec cap", config.AI_MAX_CANDIDATES],
        ["AI scan limit", max(config.AI_MAX_CANDIDATES, config.AI_SCAN_LIMIT)],
        ["AI min edge", f"{config.AI_MIN_EDGE_PCT:.1f}%"],
        ["Min AI confidence", f"{config.MIN_AI_CONFIDENCE:.0%}"],
        ["AI paper mode", config.AI_PAPER_MODE if config.PAPER_TRADING else "gate"],
        ["Strategies", f"same={config.ENABLE_SAME_MARKET_ARB} same_exec={config.ENABLE_SAME_MARKET_EXECUTION} cross={config.ENABLE_CROSS_MARKET_ARB} odds={config.ENABLE_ODDS_COMPARISON_ARB}"],
        ["Bet size", f"{config.BET_SIZE_PCT:.1f}% of bankroll"],
        ["Max bet", f"${config.MAX_BET_SIZE:.2f}"],
        ["Poll interval", f"{config.POLL_INTERVAL}s"],
        [
            "Realtime feed",
            (
                f"on ({config.REALTIME_MARKET_WS_MAX_ASSETS} assets, "
                f"{config.REALTIME_MARKET_WS_MAX_HOURS_TO_EXPIRY:.0f}h window)"
                if config.REALTIME_MARKET_WS_ENABLED
                else "off"
            ),
        ],
        [
            "Realtime gate",
            (
                f"on (spread<={config.REALTIME_GATE_MAX_SPREAD:.2f}, "
                f"depth>={config.REALTIME_GATE_MIN_DEPTH_USD:.2f})"
                if config.ENABLE_REALTIME_EXECUTION_GATE
                else "off"
            ),
        ],
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
    parser.add_argument(
        "--opportunity-report",
        action="store_true",
        help="Scan and print opportunities without trading or calling AI",
    )
    parser.add_argument(
        "--expiry-hours",
        type=float,
        default=48.0,
        help="With --opportunity-report, only inspect markets ending within this many hours",
    )
    parser.add_argument(
        "--report-limit",
        type=int,
        default=25,
        help="Maximum rows to print in --opportunity-report",
    )
    return parser.parse_args(argv)


def _log_reset_summary(summary: dict) -> None:
    removed = len(summary.get("removed") or [])
    errors = summary.get("errors") or []
    snapshot = summary.get("snapshot") or {}
    snapshot_path = snapshot.get("path")
    if snapshot_path:
        logger.warning(
            f"Fresh start archive created â€” {snapshot_path} "
            f"({len(snapshot.get('copied') or [])} file(s))"
        )
    for err in snapshot.get("errors") or []:
        logger.warning(f"Fresh start archive warning: {err}")
    logger.warning(
        f"Fresh start reset complete — removed {removed} file(s), "
        f"clear_logs={summary.get('clear_logs', False)}"
    )
    for err in errors:
        logger.warning(f"Fresh start reset warning: {err}")


def _load_open_market_ids() -> set[str]:
    """Read open market IDs directly without mutating portfolio state."""
    portfolio_file = Path("data") / "portfolio.json"
    if not portfolio_file.exists():
        return set()
    try:
        data = json.loads(portfolio_file.read_text(encoding="utf-8"))
        return {
            str(pos.get("market_id"))
            for pos in (data.get("open_positions") or {}).values()
            if pos.get("market_id")
        }
    except Exception as e:
        logger.warning(f"Opportunity report could not read portfolio state: {e}")
        return set()


def run_opportunity_report(expiry_hours: float = 48.0, limit: int = 25) -> None:
    """Print an inspection-only opportunity report for a near-expiry window."""
    window_hours = max(0.0, float(expiry_hours))
    row_limit = max(1, int(limit))

    logger.info(
        f"Running opportunity report only — expiry_window={window_hours:.1f}h, "
        "no trades, no AI calls"
    )

    markets = scanner.scan_sports_markets()
    window_markets = [
        market for market in markets
        if 0 < market.hours_to_expiry <= window_hours
    ]
    type_counts: dict[str, int] = {}
    for market in window_markets:
        market_type = market.sports_market_type or "unknown"
        type_counts[market_type] = type_counts.get(market_type, 0) + 1

    opportunities = arbitrage.find_opportunities(window_markets)
    open_market_ids = _load_open_market_ids()

    print()
    print("Opportunity Report")
    print(f"Markets scanned        : {len(markets)}")
    print(f"Markets <= {window_hours:.1f}h     : {len(window_markets)}")
    print(f"Opportunities found   : {len(opportunities)}")
    print(f"Open market conflicts : {sum(1 for o in opportunities if o.market_id in open_market_ids)}")
    if type_counts:
        by_type = ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items()))
        print(f"Window market types   : {by_type}")
    print()

    if not markets:
        print("No markets were returned. Check Gamma API/network access before interpreting this report.")
        return

    if not opportunities:
        print("No opportunities found in this expiry window using the current detector settings.")
        print("This does not mean there are no markets; it means none passed the current edge/match filters.")
        return

    rows = []
    for opp in opportunities[:row_limit]:
        ext = opp.external_odds
        if ext is not None:
            external = (
                f"{ext.home_team} vs {ext.away_team} "
                f"conf={ext.match_confidence:.2f}"
            )
        else:
            external = "-"
        rows.append([
            "yes" if opp.market_id in open_market_ids else "no",
            opp.type,
            opp.question[:60],
            opp.side,
            f"{opp.price:.3f}",
            f"{opp.edge_pct:.1f}%",
            f"{opp.raw_data.hours_to_expiry:.1f}h",
            external[:45],
            opp.market_url,
        ])

    print(tabulate(
        rows,
        headers=[
            "Already Open",
            "Type",
            "Market",
            "Outcome",
            "Price",
            "Edge",
            "Ends",
            "External Match",
            "URL",
        ],
        tablefmt="github",
    ))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    ai = AIAnalyzer()
    port = Portfolio(starting_bankroll=0.0)
    exec_ = Executor()
    comp = Compounder()
    risk = RiskManager(port)
    realtime = get_shared_feed()
    risk_journal = RiskEventJournal()
    shadow = ShadowTracker()

    startup_checks(port, exec_, ai)
    realtime.start()
    shadow.save()
    live_account_address = exec_.get_account_address() if not config.PAPER_TRADING else None

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
            port.check_day_reset()

            # Check if trading is globally blocked before doing any API calls
            trading_blocked, block_reason = risk.is_globally_blocked()

            if trading_blocked:
                open_before = len(port.state.open_positions)
                port.check_resolutions()
                open_after = len(port.state.open_positions)
                closed_count = open_before - open_after
                shadow.resolve_signals()
                with port._lock:
                    blocked_positions = [dict(p) for p in port.state.open_positions.values()]
                realtime.refresh_watchlist(open_positions=blocked_positions)

                if closed_count > 0:
                    # A position resolved — re-evaluate, may allow new entries
                    trading_blocked, block_reason = risk.is_globally_blocked()
                    logger.info(
                        f"━━ CYCLE {cycle:>4} ━━ {closed_count} position(s) resolved — "
                        f"{'resuming trading' if not trading_blocked else f'still blocked ({block_reason})'} | "
                        f"bankroll=${port.state.current_bankroll:.2f} | open={open_after}"
                    )

                if trading_blocked:
                    risk_journal.record(
                        cycle=cycle,
                        stage="global_block",
                        event="deny",
                        reason=block_reason,
                        extra={
                            "bankroll": round(port.state.current_bankroll, 4),
                            "open_positions": len(port.state.open_positions),
                        },
                        dedupe_key=f"global_block:{block_reason}",
                    )
                    logger.info(
                        f"━━ CYCLE {cycle:>4} ━━ IDLE ({block_reason}) | "
                        f"bankroll=${port.state.current_bankroll:.2f} | "
                        f"open={len(port.state.open_positions)}"
                    )

            if not trading_blocked:
                # 1. Scan markets
                markets = scanner.scan_sports_markets()
                with port._lock:
                    open_positions_snapshot = [dict(p) for p in port.state.open_positions.values()]
                realtime.refresh_watchlist(markets=markets, open_positions=open_positions_snapshot)

                # 2. Detect arbitrage opportunities
                opportunities = arbitrage.find_opportunities(markets)
                new_shadow_signals = shadow.track_opportunities(opportunities)
                if new_shadow_signals:
                    logger.info(f"  shadow tracker recorded {new_shadow_signals} new signal(s)")

                # 3. Pre-filter — skip markets already open, apply edge threshold.
                #    Scan a slightly wider AI window so the same top rejects do not
                #    starve the rest of the opportunity set every cycle.
                with port._lock:
                    open_market_ids = {
                        p.get("market_id") for p in open_positions_snapshot
                    }
                min_required_edge = max(config.MIN_EDGE_PCT, config.AI_MIN_EDGE_PCT)
                executable_opportunities = [
                    o for o in opportunities
                    if o.type != "same_market" or config.ENABLE_SAME_MARKET_EXECUTION
                ]
                skipped_same_market_count = len(opportunities) - len(executable_opportunities)
                skipped_open_count = sum(
                    1 for o in executable_opportunities if o.market_id in open_market_ids
                )
                skipped_edge_count = sum(
                    1 for o in executable_opportunities if o.edge_pct < min_required_edge
                )
                ranked_candidates = sorted(
                    [
                        o for o in executable_opportunities
                        if o.edge_pct >= min_required_edge
                        and o.market_id not in open_market_ids
                    ],
                    key=lambda o: (o.value_score, o.edge_pct),
                    reverse=True,
                )

                ai_scan_limit = max(config.AI_MAX_CANDIDATES, config.AI_SCAN_LIMIT)
                filtered = []
                skipped_closed_count = 0
                for opp in ranked_candidates:
                    if len(filtered) >= ai_scan_limit:
                        break
                    verified_at = time.monotonic()
                    is_open, _, reason = scanner.verify_market_open(opp.condition_id, opp.question)
                    if not is_open:
                        skipped_closed_count += 1
                        risk_journal.record(
                            cycle=cycle,
                            stage="pre_filter",
                            event="deny",
                            reason=f"market_{reason}",
                            opp=opp,
                        )
                        logger.info(
                            f"  skipped closed market {opp.market_id} "
                            f"({reason}) | {opp.question[:50]}"
                        )
                        continue
                    filtered.append((opp, verified_at))

                logger.info(
                    f"━━ CYCLE {cycle:>4} ━━ "
                    f"markets={len(markets)} | "
                    f"opps={len(opportunities)} | "
                    f"queued={len(filtered)} | "
                    f"bankroll=${port.state.current_bankroll:.2f} | "
                    f"open={len(port.state.open_positions)}"
                )

                if (
                    skipped_same_market_count
                    or skipped_open_count
                    or skipped_edge_count
                    or skipped_closed_count
                ):
                    risk_journal.record(
                        cycle=cycle,
                        stage="pre_filter",
                        event="summary",
                        reason="candidate_filtering",
                        extra={
                            "same_market_exec_disabled": skipped_same_market_count,
                            "already_open": skipped_open_count,
                            "below_edge": skipped_edge_count,
                            "closed_or_inactive": skipped_closed_count,
                            "ranked_candidates": len(ranked_candidates),
                            "queued": len(filtered),
                        },
                        dedupe_key=(
                            "pre_filter_summary:"
                            f"{skipped_same_market_count}:"
                            f"{skipped_open_count}:"
                            f"{skipped_edge_count}:"
                            f"{skipped_closed_count}:"
                            f"{len(filtered)}"
                        ),
                    )

                # 4. AI validation
                validated = []
                advisory_block_count = 0
                for opp, verified_at in filtered:
                    if len(validated) >= config.AI_MAX_CANDIDATES:
                        break
                    analysis = ai.analyze(opp)
                    if analysis is None:
                        risk_journal.record(
                            cycle=cycle,
                            stage="ai_gate",
                            event="deny",
                            reason="ai_unavailable",
                            opp=opp,
                        )
                        if not config.ANTHROPIC_API_KEY:
                            logger.critical(
                                "ANTHROPIC_API_KEY not set — AI gate disabled, "
                                "skipping ALL opportunities. Set the key to enable trading."
                            )
                        continue

                    if analysis.supports_candidate(opp.side, opp.price):
                        validated.append((opp, analysis, verified_at))
                    elif config.PAPER_TRADING and config.AI_PAPER_MODE == "advisory":
                        advisory_block_count += 1
                        if analysis.recommended_side == "SKIP":
                            advisory_reason = "paper_advisory_skip"
                        elif analysis.recommended_side != opp.side:
                            advisory_reason = "paper_advisory_side_mismatch"
                        elif analysis.predicted_probability <= opp.price:
                            advisory_reason = "paper_advisory_model_below_price"
                        elif analysis.confidence < config.MIN_AI_CONFIDENCE:
                            advisory_reason = "paper_advisory_low_confidence"
                        elif not analysis.edge_detected:
                            advisory_reason = "paper_advisory_no_edge"
                        else:
                            advisory_reason = "paper_advisory_blocked"
                        risk_journal.record(
                            cycle=cycle,
                            stage="ai_gate",
                            event="deny",
                            reason=advisory_reason,
                            opp=opp,
                            extra={
                                "ai_side": analysis.recommended_side,
                                "ai_confidence": round(analysis.confidence, 4),
                                "edge_detected": bool(analysis.edge_detected),
                            },
                        )
                        logger.info(
                            f"  paper advisory blocked for {opp.market_id} "
                            f"(ai_side={analysis.recommended_side}, "
                            f"edge={analysis.edge_detected}, conf={analysis.confidence:.2f})"
                        )
                    elif analysis.is_valid:
                        if analysis.recommended_side != opp.side:
                            risk_journal.record(
                                cycle=cycle,
                                stage="ai_gate",
                                event="deny",
                                reason="ai_side_mismatch",
                                opp=opp,
                                extra={
                                    "ai_side": analysis.recommended_side,
                                    "ai_confidence": round(analysis.confidence, 4),
                                    "edge_detected": bool(analysis.edge_detected),
                                    "predicted_probability": round(analysis.predicted_probability, 4),
                                    "candidate_price": round(opp.price, 4),
                                },
                            )
                            logger.info(
                                f"  skipped AI side mismatch for {opp.market_id} "
                                f"(opp={opp.side}, ai={analysis.recommended_side})"
                            )
                        else:
                            risk_journal.record(
                                cycle=cycle,
                                stage="ai_gate",
                                event="deny",
                                reason="ai_model_below_price",
                                opp=opp,
                                extra={
                                    "ai_side": analysis.recommended_side,
                                    "ai_confidence": round(analysis.confidence, 4),
                                    "edge_detected": bool(analysis.edge_detected),
                                    "predicted_probability": round(analysis.predicted_probability, 4),
                                    "candidate_price": round(opp.price, 4),
                                },
                            )
                            logger.info(
                                f"  skipped AI model below price for {opp.market_id} "
                                f"(side={opp.side}, model={analysis.predicted_probability:.3f}, "
                                f"price={opp.price:.3f})"
                            )
                    else:
                        risk_journal.record(
                            cycle=cycle,
                            stage="ai_gate",
                            event="deny",
                            reason="ai_invalid",
                            opp=opp,
                            extra={
                                "ai_side": analysis.recommended_side,
                                "ai_confidence": round(analysis.confidence, 4),
                                "edge_detected": bool(analysis.edge_detected),
                            },
                        )

                if validated:
                    logger.info(f"  ✓ {len(validated)} passed AI validation → executing")
                elif advisory_block_count:
                    logger.info(
                        f"  ✗ 0/{len(filtered)} passed AI validation "
                        f"({advisory_block_count} advisory candidate(s) blocked by hard guardrails)"
                    )
                elif not filtered:
                    logger.info(
                        "  no AI candidates after filters "
                        f"(same_market_exec_disabled={skipped_same_market_count}, "
                        f"already_open={skipped_open_count}, "
                        f"below_edge={skipped_edge_count}, "
                        f"closed_or_inactive={skipped_closed_count})"
                    )
                else:
                    logger.info(f"  ✗ 0/{len(filtered)} passed AI validation")

                # 5. Execute
                trades_placed = 0
                for opp, analysis, verified_at in validated:
                    size = risk.get_position_size(
                        adjusted_bet_pct=comp.current_bet_pct,
                        edge_pct=opp.edge_pct,
                        price=opp.price,
                        ai_confidence=analysis.confidence,
                    )
                    allowed, reason = risk.can_trade(opp, size)

                    if not allowed:
                        risk_journal.record(
                            cycle=cycle,
                            stage="risk_gate",
                            event="deny",
                            reason=reason,
                            opp=opp,
                            extra={"proposed_size": round(size, 4)},
                        )
                        logger.info(f"  blocked ({reason})")
                        continue

                    verify_age = time.monotonic() - verified_at
                    if verify_age > EXECUTION_VERIFY_MAX_AGE_SECONDS:
                        is_open, _, status_reason = scanner.verify_market_open(opp.condition_id, opp.question)
                        if not is_open:
                            risk_journal.record(
                                cycle=cycle,
                                stage="execution_check",
                                event="deny",
                                reason=f"market_{status_reason}",
                                opp=opp,
                                extra={"verify_age_seconds": round(verify_age, 3)},
                            )
                            logger.info(
                                f"  blocked (market_{status_reason}) | {opp.question[:50]}"
                            )
                            continue

                    result = exec_.place_order(opp, size)
                    if result and result.get("success"):
                        risk_journal.record(
                            cycle=cycle,
                            stage="execution",
                            event="accept",
                            reason="order_recorded",
                            opp=opp,
                            extra={
                                "fill_price": float(result.get("fill_price", opp.price) or opp.price),
                                "fill_size": float(result.get("fill_size", 0) or 0),
                                "simulated": bool(result.get("simulated", False)),
                            },
                        )
                        port.record_trade(opp, result, analysis)
                        comp.update(port.state)
                        trades_placed += 1
                        open_market_ids.add(opp.market_id)

                if trades_placed:
                    logger.info(f"  → {trades_placed} trade(s) placed this cycle")

                # 6. Check resolved markets
                port.check_resolutions()
                shadow.resolve_signals()

            # Always check early exits and resolutions even when trading is
            # blocked — existing positions should be managed regardless.
            early_exits = port.check_early_exits()
            if early_exits:
                logger.info(f"  → {early_exits} position(s) exited early")
                comp.update(port.state)

            # 7. Periodic bankroll sync (every ~60 seconds)
            bankroll_sync_counter += 1
            if bankroll_sync_counter >= (60 // config.POLL_INTERVAL):
                bankroll_sync_counter = 0
                balance = exec_.get_usdc_balance()
                if balance is not None:
                    port.sync_bankroll(balance)
                if live_account_address:
                    port.reconcile_live_account(live_account_address)

            # 8. Status log (every ~60 seconds)
            if cycle % max(1, 60 // config.POLL_INTERVAL) == 0:
                port.log_status()
                shadow.log_summary()
                ai.log_usage()
                realtime.log_status()

            # 9. Periodic save
            port.maybe_save()
            shadow.maybe_save()

        except Exception as e:
            logger.error(f"Main loop error (cycle {cycle}): {e}", exc_info=True)

        # Sleep for remainder of poll interval
        elapsed = time.monotonic() - cycle_start
        sleep_time = max(0.0, config.POLL_INTERVAL - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)

    # Graceful shutdown
    logger.info("Shutting down...")
    realtime.stop()
    port.save()
    shadow.save()
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

    if args.opportunity_report:
        run_opportunity_report(
            expiry_hours=args.expiry_hours,
            limit=args.report_limit,
        )
        return

    run()


if __name__ == "__main__":
    main()
