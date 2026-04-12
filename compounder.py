"""
Profit Compounder — adjusts effective bet size percentage as bankroll grows.
Uses win-rate based scaling (not streak-based) and logs milestones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from logger_setup import get_logger

if TYPE_CHECKING:
    from portfolio import PortfolioState

logger = get_logger(__name__)

MILESTONES = [10, 50, 100, 500, 1_000, 5_000, 10_000, 50_000,
              100_000, 500_000, 1_000_000, 3_300_000]

# Minimum trades before adjusting bet size based on win rate
MIN_TRADES_FOR_ADJUSTMENT = 10
MAX_BET_PCT = 4.0


class Compounder:
    def __init__(self):
        self._current_bet_pct: float = config.BET_SIZE_PCT
        self._last_milestone: float = 0.0

    @property
    def current_bet_pct(self) -> float:
        return self._current_bet_pct

    def update(self, state: "PortfolioState") -> float:
        """Call after each trade result. Returns updated bet_size_pct.

        Scales bet size based on historical win rate rather than streaks.
        A >55% win rate earns a modest boost; <45% triggers a reduction.
        Below MIN_TRADES_FOR_ADJUSTMENT trades, stays at base percentage.
        """
        total = state.total_trades
        losses = state.consecutive_losses

        # Reduce on consecutive losses (protective, not streak-chasing)
        if losses >= 3:
            self._current_bet_pct = max(config.BET_SIZE_PCT * 0.5, 1.0)
            self._check_milestones(state.current_bankroll)
            return self._current_bet_pct

        # Need enough data to adjust
        if total < MIN_TRADES_FOR_ADJUSTMENT:
            self._current_bet_pct = config.BET_SIZE_PCT
            self._check_milestones(state.current_bankroll)
            return self._current_bet_pct

        win_rate = state.winning_trades / max(1, total)
        if win_rate >= 0.55:
            # Modest boost: +0.5% for sustained profitability
            self._current_bet_pct = min(config.BET_SIZE_PCT + 0.5, MAX_BET_PCT)
        elif win_rate < 0.45:
            # Reduce: bot is not profitable at current thresholds
            self._current_bet_pct = max(config.BET_SIZE_PCT * 0.75, 1.0)
        else:
            self._current_bet_pct = config.BET_SIZE_PCT

        self._check_milestones(state.current_bankroll)
        return self._current_bet_pct

    def _check_milestones(self, bankroll: float) -> None:
        for milestone in MILESTONES:
            if bankroll >= milestone > self._last_milestone:
                logger.info(
                    f"*** MILESTONE: bankroll reached ${milestone:,.0f}! "
                    f"Current bet size: {self._current_bet_pct:.2f}% ***"
                )
                self._last_milestone = float(milestone)
                break
