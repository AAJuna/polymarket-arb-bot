"""
Profit Compounder — adjusts effective bet size percentage as bankroll grows.
Boosts bet size on win streaks, resets after losses, logs milestones.
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

# Each 3 consecutive wins → +WIN_STREAK_BOOST% (capped at MAX_BET_PCT)
WIN_STREAK_BOOST = 0.5
MAX_BET_PCT = 4.0


class Compounder:
    def __init__(self):
        self._current_bet_pct: float = config.BET_SIZE_PCT
        self._last_milestone: float = 0.0

    @property
    def current_bet_pct(self) -> float:
        return self._current_bet_pct

    def update(self, state: "PortfolioState") -> float:
        """Call after each trade result. Returns updated bet_size_pct."""
        wins = state.consecutive_wins
        losses = state.consecutive_losses

        if losses > 0:
            # Reset to base on any loss
            self._current_bet_pct = config.BET_SIZE_PCT
        elif wins >= 3:
            # +0.5% per 3-win streak, capped
            boosts = wins // 3
            boosted = config.BET_SIZE_PCT + boosts * WIN_STREAK_BOOST
            self._current_bet_pct = min(boosted, MAX_BET_PCT)

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
