"""
Risk Manager — gates every trade. Pure read of PortfolioState; never mutates it.
All monetary decisions pass through here before execution.
"""

from __future__ import annotations

import time
from datetime import timezone
from typing import TYPE_CHECKING, Tuple

import config
from logger_setup import get_logger
from realtime_feed import get_shared_feed
from utils import compute_orderbook_depth, utcnow

if TYPE_CHECKING:
    from arbitrage import Opportunity
    from portfolio import Portfolio

logger = get_logger(__name__)


class RiskManager:
    def __init__(self, portfolio: "Portfolio"):
        self._portfolio = portfolio

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def get_position_size(self, adjusted_bet_pct: float | None = None) -> float:
        """Return the dollar size for the next trade.

        Returns 0.0 if trading should be fully blocked.
        adjusted_bet_pct comes from Compounder; falls back to config.BET_SIZE_PCT.
        """
        state = self._portfolio.state
        bankroll = state.current_bankroll
        equity = self._equity()

        if bankroll <= 0:
            return 0.0

        bet_pct = adjusted_bet_pct if adjusted_bet_pct is not None else config.BET_SIZE_PCT
        size = equity * (bet_pct / 100.0)

        # Drawdown reductions
        drawdown = self._drawdown()
        if drawdown >= config.DRAWDOWN_STOP_THRESHOLD:
            logger.warning(f"Drawdown stop triggered ({drawdown:.1%}) — blocking all trades")
            return 0.0
        if drawdown >= config.DRAWDOWN_REDUCE_THRESHOLD:
            size *= 0.5
            logger.debug(f"Drawdown reduction active ({drawdown:.1%}) — bet halved to ${size:.2f}")

        # Consecutive loss reductions
        if state.consecutive_losses >= config.CONSECUTIVE_LOSS_PAUSE:
            if not self._pause_expired():
                logger.warning(
                    f"{state.consecutive_losses} consecutive losses — "
                    f"pause active until {state.pause_until}"
                )
                return 0.0
        elif state.consecutive_losses >= config.CONSECUTIVE_LOSS_REDUCE:
            size *= 0.5
            logger.debug(
                f"Consecutive loss reduction ({state.consecutive_losses} losses) — "
                f"bet halved to ${size:.2f}"
            )

        # Floor / ceiling
        minimum_size = min(config.INITIAL_BET_SIZE, bankroll)
        size = max(minimum_size, min(size, config.MAX_BET_SIZE, bankroll))
        return size

    # ------------------------------------------------------------------
    # Global block check (before any API calls)
    # ------------------------------------------------------------------

    def is_globally_blocked(self) -> Tuple[bool, str]:
        """Quick check if trading is blocked globally — no API calls should happen if True."""
        state = self._portfolio.state

        # Drawdown stop
        if self._drawdown() >= config.DRAWDOWN_STOP_THRESHOLD:
            return True, f"drawdown_stop ({self._drawdown():.1%})"

        # Daily loss limit (realized only)
        day_start = state.day_start_bankroll
        if day_start > 0:
            equity = self._equity()
            daily_loss = (day_start - equity) / day_start
            if daily_loss >= config.DAILY_LOSS_LIMIT_PCT:
                return True, f"daily_loss_limit ({daily_loss:.1%})"

        # Consecutive loss pause
        if state.consecutive_losses >= config.CONSECUTIVE_LOSS_PAUSE:
            if not self._pause_expired():
                return True, f"consecutive_loss_pause ({state.consecutive_losses} losses)"

        return False, ""

    # ------------------------------------------------------------------
    # Trade gate
    # ------------------------------------------------------------------

    def can_trade(self, opp: "Opportunity", proposed_size: float) -> Tuple[bool, str]:
        """Return (allowed, reason_if_blocked).

        Does NOT modify portfolio state.
        """
        state = self._portfolio.state
        bankroll = state.current_bankroll

        if proposed_size <= 0:
            return False, "position_size_zero"

        # Hard stop — drawdown
        drawdown = self._drawdown()
        if drawdown >= config.DRAWDOWN_STOP_THRESHOLD:
            return False, f"drawdown_stop ({drawdown:.1%})"

        # Pause — consecutive losses
        if state.consecutive_losses >= config.CONSECUTIVE_LOSS_PAUSE:
            if not self._pause_expired():
                return False, f"consecutive_loss_pause ({state.consecutive_losses} losses)"
            else:
                # Pause has expired — log and allow
                logger.info("Consecutive loss pause expired — resuming trading")

        # Daily loss limit — only count realized losses, not open position cost
        day_start = state.day_start_bankroll
        if day_start > 0:
            equity = self._equity()
            daily_loss = (day_start - equity) / day_start
            if daily_loss >= config.DAILY_LOSS_LIMIT_PCT:
                return False, f"daily_loss_limit ({daily_loss:.1%})"

        # Total exposure
        open_exposure = sum(
            p.get("cost_basis", 0)
            for p in state.open_positions.values()
        )
        equity = self._equity()
        max_exposure = equity * (config.MAX_EXPOSURE_PCT / 100.0)
        if (open_exposure + proposed_size) > max_exposure:
            return False, (
                f"max_exposure_exceeded "
                f"(current={open_exposure:.2f}, adding={proposed_size:.2f}, max={max_exposure:.2f})"
            )

        # Market concentration
        market_exposure = sum(
            p.get("cost_basis", 0)
            for p in state.open_positions.values()
            if p.get("market_id") == opp.market_id
        )
        max_concentration = equity * (config.MAX_MARKET_CONCENTRATION_PCT / 100.0)
        if (market_exposure + proposed_size) > max_concentration:
            return False, (
                f"market_concentration_exceeded "
                f"({opp.market_id}: {market_exposure:.2f} + {proposed_size:.2f} > {max_concentration:.2f})"
            )

        # Realtime execution gate — only enforce when the feed is live.
        if config.ENABLE_REALTIME_EXECUTION_GATE:
            feed = get_shared_feed()
            if feed.enabled and feed.is_connected():
                spread = feed.get_spread(opp.token_id)
                if spread is not None and spread > config.REALTIME_GATE_MAX_SPREAD:
                    return False, (
                        f"realtime_spread_too_wide "
                        f"(spread={spread:.3f}, max={config.REALTIME_GATE_MAX_SPREAD:.3f})"
                    )

                asks = feed.get_orderbook_asks(opp.token_id)
                if asks:
                    _, depth_usd = compute_orderbook_depth(
                        asks,
                        config.REALTIME_GATE_MIN_DEPTH_USD,
                    )
                    if depth_usd < config.REALTIME_GATE_MIN_DEPTH_USD:
                        return False, (
                            f"realtime_depth_insufficient "
                            f"(depth={depth_usd:.2f}, min={config.REALTIME_GATE_MIN_DEPTH_USD:.2f})"
                        )

        # Max concurrent open orders
        if len(state.open_positions) >= config.MAX_CONCURRENT_ORDERS:
            return False, f"max_concurrent_orders ({len(state.open_positions)})"

        return True, ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _drawdown(self) -> float:
        """Fraction of peak bankroll lost (0.0 = no loss, 1.0 = total loss)."""
        state = self._portfolio.state
        peak = state.peak_bankroll
        if peak <= 0:
            return 0.0
        return max(0.0, (peak - self._equity()) / peak)

    def _equity(self) -> float:
        """Cash plus cost basis of open positions."""
        state = self._portfolio.state
        open_cost = sum(p.get("cost_basis", 0) for p in state.open_positions.values())
        return state.current_bankroll + open_cost

    def _pause_expired(self) -> bool:
        """True if the consecutive-loss pause window has elapsed."""
        state = self._portfolio.state
        if not state.pause_until:
            # Set it now
            import datetime
            pause_end = utcnow() + datetime.timedelta(minutes=config.PAUSE_DURATION_MINUTES)
            with self._portfolio._lock:
                self._portfolio.state.pause_until = pause_end.isoformat()
            logger.warning(
                f"Trading paused for {config.PAUSE_DURATION_MINUTES} min "
                f"(until {self._portfolio.state.pause_until})"
            )
            return False

        try:
            pause_end_str = state.pause_until
            if pause_end_str.endswith("Z"):
                pause_end_str = pause_end_str[:-1] + "+00:00"
            from datetime import datetime as dt
            pause_end = dt.fromisoformat(pause_end_str)
            if pause_end.tzinfo is None:
                pause_end = pause_end.replace(tzinfo=timezone.utc)

            if utcnow() >= pause_end:
                with self._portfolio._lock:
                    self._portfolio.state.pause_until = None
                    self._portfolio.state.consecutive_losses = 0
                return True
            return False
        except Exception:
            return True
