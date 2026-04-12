"""
BTC 5-minute signal engine.

Three-layer probability model:
  1. Market-implied baseline — Polymarket's own Up/Down pricing
  2. Momentum — short-term BTC price trend (primary edge source)
  3. Statistical — Gaussian model when strike is available

The key insight: we can't reliably capture Polymarket's exact strike
(set by Chainlink at the precise window start). Instead, we use the
market's own pricing as the baseline and look for momentum divergence
as our edge signal.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from btc import config_btc as cfg
from btc.rtds_feed import RtdsFeed
from logger_setup import get_logger

logger = get_logger(__name__)

MINUTES_PER_YEAR = 365.25 * 24 * 60


@dataclass
class BtcSignal:
    side: str  # "UP" or "DOWN"
    model_probability: float  # P(chosen side wins)
    market_price: float  # current Polymarket price for that side
    edge_pct: float  # (model_prob - market_price) * 100
    confidence: float  # 0-1
    volatility: float  # annualized realized vol
    time_remaining_sec: float
    btc_price: float
    strike_price: float
    # Layer breakdown
    statistical_prob: float  # P(Up) from Gaussian model
    momentum_adj: float  # momentum adjustment applied
    orderflow_adj: float  # order flow adjustment applied


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class SignalEngine:
    """Compute UP/DOWN probability for a 5-minute BTC window."""

    def __init__(self, rtds: RtdsFeed) -> None:
        self._rtds = rtds
        self._strike_price: Optional[float] = None
        self._window_start: Optional[datetime] = None
        self._window_end_sec: float = 300.0  # 5 minutes

    def set_window(
        self,
        strike_price: float,
        window_start: datetime,
        window_duration_sec: float = 300.0,
    ) -> None:
        """Set the strike price and window timing for current market."""
        self._strike_price = strike_price
        self._window_start = window_start
        self._window_end_sec = window_duration_sec
        logger.info(
            f"Signal engine: strike=${strike_price:,.2f}  "
            f"window={window_duration_sec:.0f}s"
        )

    def reset(self) -> None:
        """Clear state for a new window."""
        self._strike_price = None
        self._window_start = None

    @property
    def is_ready(self) -> bool:
        return self._window_start is not None

    def get_signal(
        self,
        up_price: float,
        down_price: float,
    ) -> Optional[BtcSignal]:
        """Compute the current signal given market prices.

        Returns None if insufficient data or window not set.
        """
        if not self.is_ready:
            return None

        btc_price = self._rtds.get_btc_price()
        if btc_price is None:
            return None

        now = datetime.now(timezone.utc)
        elapsed = (now - self._window_start).total_seconds()
        time_remaining = max(0.0, self._window_end_sec - elapsed)

        if time_remaining <= 0:
            return None

        vol = self._compute_volatility()

        # Layer 1: Market-implied baseline
        # The market's own pricing is our starting point — it already
        # reflects the true strike from Chainlink.
        market_p_up = up_price / (up_price + down_price) if (up_price + down_price) > 0 else 0.5

        # Layer 2: Momentum (primary edge source)
        # If BTC is trending strongly and the market hasn't adjusted yet,
        # we have edge.
        momentum_adj = self._momentum_adjustment(vol)

        # Layer 3: Statistical (secondary, only if strike available)
        stat_adj = 0.0
        stat_p_up = market_p_up
        if self._strike_price and self._strike_price > 0:
            stat_p_up = self._statistical_probability(
                btc_price, self._strike_price, time_remaining, vol
            )
            # Use deviation from market as an adjustment, not absolute value
            stat_adj = (stat_p_up - market_p_up) * 0.3  # dampen by 70%

        # Combined model: start from market baseline, add momentum + stat adjustment
        p_up = market_p_up + momentum_adj + stat_adj
        p_up = max(0.01, min(0.99, p_up))
        p_down = 1.0 - p_up

        # Pick the side with more edge
        up_edge = (p_up - up_price) * 100
        down_edge = (p_down - down_price) * 100

        if up_edge >= down_edge:
            side = "UP"
            model_prob = p_up
            market_price = up_price
            edge_pct = up_edge
        else:
            side = "DOWN"
            model_prob = p_down
            market_price = down_price
            edge_pct = down_edge

        # Confidence
        confidence = self._compute_confidence(
            momentum_adj, stat_adj, time_remaining, vol
        )

        return BtcSignal(
            side=side,
            model_probability=model_prob,
            market_price=market_price,
            edge_pct=edge_pct,
            confidence=confidence,
            volatility=vol,
            time_remaining_sec=time_remaining,
            btc_price=btc_price,
            strike_price=self._strike_price or 0.0,
            statistical_prob=stat_p_up,
            momentum_adj=momentum_adj,
            orderflow_adj=stat_adj,
        )

    # ------------------------------------------------------------------
    # Layer 2: Momentum (primary edge)
    # ------------------------------------------------------------------

    def _momentum_adjustment(self, vol_annual: float) -> float:
        """Short-term momentum signal from recent price movement.

        This is the primary edge source: if BTC is trending strongly
        in the last 30-60 seconds, the market price likely hasn't
        fully adjusted yet.
        """
        history = self._rtds.get_price_history(seconds=60)
        if len(history) < 10:
            return 0.0

        # Linear regression slope over last 60 seconds
        n = len(history)
        t0 = history[0][0]
        xs = [t - t0 for t, _ in history]
        ys = [p for _, p in history]

        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        if den < 1e-10:
            return 0.0

        slope = num / den  # $/second

        # Normalize: slope relative to price, scaled by vol
        price = ys[-1] if ys[-1] > 0 else 1.0
        slope_pct_per_sec = slope / price

        # Convert to a z-score using per-second vol
        if vol_annual > 0:
            vol_per_sec = vol_annual / math.sqrt(MINUTES_PER_YEAR * 60)
            z = slope_pct_per_sec / max(vol_per_sec, 1e-10)
        else:
            z = 0.0

        # Map z-score to probability adjustment, capped
        adj = z * 0.03  # 3% per 1 sigma of momentum
        return max(-cfg.MOMENTUM_CAP, min(cfg.MOMENTUM_CAP, adj))

    # ------------------------------------------------------------------
    # Layer 3: Statistical (Gaussian)
    # ------------------------------------------------------------------

    def _statistical_probability(
        self,
        current_price: float,
        strike: float,
        time_remaining_sec: float,
        vol_annual: float,
    ) -> float:
        """P(BTC >= strike at expiry) using Gaussian model."""
        if current_price <= 0 or strike <= 0:
            return 0.5
        if time_remaining_sec <= 0:
            return 1.0 if current_price >= strike else 0.0

        time_remaining_min = time_remaining_sec / 60.0
        sigma_t = vol_annual * math.sqrt(time_remaining_min / MINUTES_PER_YEAR)

        if sigma_t < 1e-10:
            return 1.0 if current_price >= strike else 0.0

        d = math.log(current_price / strike) / sigma_t
        return _normal_cdf(d)

    # ------------------------------------------------------------------
    # Volatility
    # ------------------------------------------------------------------

    def _compute_volatility(self) -> float:
        """Realized volatility from RTDS price history (annualized)."""
        history = self._rtds.get_price_history(seconds=cfg.VOLATILITY_LOOKBACK_SEC)

        if len(history) < 30:
            return cfg.VOLATILITY_DEFAULT

        # Sample at ~1-second intervals to reduce noise
        sampled = self._resample(history, interval_sec=1.0)
        if len(sampled) < 20:
            return cfg.VOLATILITY_DEFAULT

        # Log returns
        log_returns = []
        for i in range(1, len(sampled)):
            if sampled[i - 1][1] > 0 and sampled[i][1] > 0:
                lr = math.log(sampled[i][1] / sampled[i - 1][1])
                log_returns.append(lr)

        if len(log_returns) < 10:
            return cfg.VOLATILITY_DEFAULT

        # Exponential weighting (more weight to recent)
        half_life = len(log_returns) / 3
        weights = []
        for i in range(len(log_returns)):
            age = len(log_returns) - 1 - i
            w = math.exp(-age * math.log(2) / half_life) if half_life > 0 else 1.0
            weights.append(w)

        total_w = sum(weights)
        w_mean = sum(r * w for r, w in zip(log_returns, weights)) / total_w
        w_var = sum(w * (r - w_mean) ** 2 for r, w in zip(log_returns, weights)) / total_w
        sigma_1s = math.sqrt(w_var)

        # Annualize
        seconds_per_year = 365.25 * 24 * 3600
        vol_annual = sigma_1s * math.sqrt(seconds_per_year)

        # Clamp
        vol_annual = max(cfg.VOLATILITY_FLOOR, min(cfg.VOLATILITY_CEILING, vol_annual))
        return vol_annual

    @staticmethod
    def _resample(
        history: list[tuple[float, float]], interval_sec: float = 1.0
    ) -> list[tuple[float, float]]:
        """Resample irregular tick data to fixed intervals."""
        if not history:
            return []

        result = [history[0]]
        next_time = history[0][0] + interval_sec
        last_price = history[0][1]

        for ts, price in history[1:]:
            last_price = price
            if ts >= next_time:
                result.append((ts, price))
                next_time = ts + interval_sec

        return result

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    def _compute_confidence(
        self,
        momentum_adj: float,
        stat_adj: float,
        time_remaining_sec: float,
        vol: float,
    ) -> float:
        """Confidence score 0-1 based on data quality and signal strength."""
        score = 1.0

        # Penalize if volatility is at default (insufficient data)
        if abs(vol - cfg.VOLATILITY_DEFAULT) < 0.01:
            score *= 0.6

        # Penalize if momentum is near zero (no directional signal)
        if abs(momentum_adj) < 0.005:
            score *= 0.5

        # Boost if momentum and stat agree in direction
        if momentum_adj != 0 and stat_adj != 0:
            if (momentum_adj > 0) == (stat_adj > 0):
                score *= 1.2
            else:
                score *= 0.7

        # Penalize very little time remaining
        if time_remaining_sec < 60:
            score *= 0.7
        elif time_remaining_sec < 120:
            score *= 0.85

        # Penalize if RTDS data is stale
        age = self._rtds.last_update_age()
        if age is not None and age > 5.0:
            score *= 0.5

        return max(0.0, min(1.0, score))
