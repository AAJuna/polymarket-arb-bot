"""
Portfolio Tracker — single source of truth for financial state.
Thread-safe. Persists to data/portfolio.json. Resumes from crashes.
"""

from __future__ import annotations

import atexit
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import config
from logger_setup import get_logger, TRADE_LEVEL
from utils import utcnow

logger = get_logger(__name__)

DATA_DIR = Path("data")
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Position:
    position_id: str
    market_id: str
    condition_id: str
    token_id: str
    side: str                   # "YES" | "NO"
    question: str
    entry_price: float
    size: float                 # shares
    cost_basis: float           # entry_price * size
    opened_at: str              # ISO string
    status: str                 # "open" | "filled" | "resolved" | "cancelled"
    order_id: str
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    closed_at: Optional[str] = None
    simulated: bool = False


@dataclass
class PortfolioState:
    starting_bankroll: float = 0.0
    current_bankroll: float = 0.0
    peak_bankroll: float = 0.0
    day_start_bankroll: float = 0.0
    day_start_date: str = ""
    open_positions: dict = field(default_factory=dict)   # position_id → Position dict
    trade_history: list = field(default_factory=list)    # list of Position dicts
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    pause_until: Optional[str] = None                    # ISO string or None


# ---------------------------------------------------------------------------
# Portfolio class
# ---------------------------------------------------------------------------

class Portfolio:
    def __init__(self, starting_bankroll: float = 0.0):
        self._lock = threading.Lock()
        self.state = PortfolioState()
        self._starting_bankroll = starting_bankroll
        self._last_save = time.monotonic()

        DATA_DIR.mkdir(exist_ok=True)
        atexit.register(self.save)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """Load state from disk. Returns True if successful."""
        if not PORTFOLIO_FILE.exists():
            logger.info("No portfolio file found — starting fresh")
            self._init_fresh()
            return False

        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self.state = PortfolioState(**{
                    k: v for k, v in data.items()
                    if k in PortfolioState.__dataclass_fields__
                })
            logger.info(
                f"Portfolio loaded: bankroll=${self.state.current_bankroll:.2f} "
                f"trades={self.state.total_trades}"
            )
            self._check_day_reset()
            return True
        except Exception as e:
            logger.error(f"Portfolio file corrupted: {e} — starting fresh")
            self._init_fresh()
            return False

    def _init_fresh(self):
        with self._lock:
            self.state = PortfolioState(
                starting_bankroll=self._starting_bankroll,
                current_bankroll=self._starting_bankroll,
                peak_bankroll=self._starting_bankroll,
                day_start_bankroll=self._starting_bankroll,
                day_start_date=utcnow().date().isoformat(),
            )

    def save(self) -> None:
        """Persist state to disk."""
        try:
            DATA_DIR.mkdir(exist_ok=True)
            with self._lock:
                data = asdict(self.state)
            with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            logger.debug("Portfolio saved")
            self._last_save = time.monotonic()
        except Exception as e:
            logger.error(f"Failed to save portfolio: {e}")

    def maybe_save(self) -> None:
        """Save if the save interval has elapsed."""
        if time.monotonic() - self._last_save >= config.PORTFOLIO_SAVE_INTERVAL:
            self.save()

    # ------------------------------------------------------------------
    # Trade recording
    # ------------------------------------------------------------------

    def record_trade(self, opp, order_result: dict, analysis=None) -> Position:
        """Record an executed order as an open position."""
        pos = Position(
            position_id=str(uuid4()),
            market_id=opp.market_id,
            condition_id=opp.condition_id,
            token_id=opp.token_id,
            side=opp.side,
            question=opp.question,
            entry_price=float(order_result.get("fill_price", opp.price)),
            size=float(order_result.get("fill_size", 0)),
            cost_basis=float(order_result.get("fill_cost", 0)),
            opened_at=utcnow().isoformat(),
            status="open",
            order_id=order_result.get("orderID", ""),
            simulated=bool(order_result.get("simulated", False)),
        )

        with self._lock:
            self.state.open_positions[pos.position_id] = asdict(pos)
            self.state.current_bankroll -= pos.cost_basis
            self._update_peaks()

        ai_conf = f" AI={analysis.confidence:.2f}" if analysis else ""
        logger.log(
            TRADE_LEVEL,
            f"TRADE OPEN | {pos.question[:50]} | {pos.side} | "
            f"size={pos.size:.2f} @ ${pos.entry_price:.3f} | "
            f"cost=${pos.cost_basis:.2f} | edge={opp.edge_pct:.1f}%{ai_conf}"
        )
        return pos

    def close_position(self, position_id: str, exit_price: float, outcome_won: bool) -> float:
        """Mark a position as resolved and compute P&L."""
        with self._lock:
            pos_dict = self.state.open_positions.get(position_id)
            if not pos_dict:
                return 0.0

            pos = Position(**pos_dict)
            if outcome_won:
                # Each winning share redeems for $1.00
                pnl = pos.size - pos.cost_basis
            else:
                pnl = -pos.cost_basis

            pos.exit_price = exit_price
            pos.pnl = pnl
            pos.closed_at = utcnow().isoformat()
            pos.status = "resolved"

            del self.state.open_positions[position_id]
            self.state.trade_history.append(asdict(pos))

            self.state.current_bankroll += pos.size if outcome_won else 0.0
            self.state.total_trades += 1

            if pnl > 0:
                self.state.winning_trades += 1
                self.state.consecutive_wins += 1
                self.state.consecutive_losses = 0
            else:
                self.state.consecutive_losses += 1
                self.state.consecutive_wins = 0

            self._update_peaks()

        logger.log(
            TRADE_LEVEL,
            f"TRADE CLOSE | {pos.question[:50]} | "
            f"{'WON' if outcome_won else 'LOST'} | "
            f"pnl=${pnl:+.2f} | bankroll=${self.state.current_bankroll:.2f}"
        )
        return pnl

    # ------------------------------------------------------------------
    # Resolution checking
    # ------------------------------------------------------------------

    def check_resolutions(self) -> None:
        """Check if any open positions have been resolved on-chain."""
        import scanner as sc

        with self._lock:
            open_ids = list(self.state.open_positions.keys())

        for pid in open_ids:
            with self._lock:
                pos_dict = self.state.open_positions.get(pid)
            if not pos_dict:
                continue

            pos = Position(**pos_dict)
            try:
                market_status = sc.get_market_status(pos.condition_id)
                if not market_status:
                    continue

                resolved = bool(market_status.get("resolved"))
                closed = bool(market_status.get("closed"))

                if resolved and closed:
                    # Determine winner
                    outcome_prices = market_status.get("outcomePrices") or ["0.5", "0.5"]
                    yes_resolved = float(outcome_prices[0]) >= 0.99

                    outcome_won = (pos.side == "YES" and yes_resolved) or \
                                  (pos.side == "NO" and not yes_resolved)

                    self.close_position(pid, 1.0 if outcome_won else 0.0, outcome_won)
            except Exception as e:
                logger.warning(f"Resolution check failed for {pid}: {e}")

    # ------------------------------------------------------------------
    # Bankroll sync
    # ------------------------------------------------------------------

    def sync_bankroll(self, usdc_balance: float) -> None:
        """Sync bankroll against actual CLOB USDC balance."""
        with self._lock:
            old = self.state.current_bankroll
            self.state.current_bankroll = usdc_balance
            self._update_peaks()
        if abs(old - usdc_balance) > 0.01:
            logger.debug(f"Bankroll synced: ${old:.2f} → ${usdc_balance:.2f}")

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def compute_metrics(self) -> dict:
        with self._lock:
            history = [Position(**p) for p in self.state.trade_history]
            bankroll = self.state.current_bankroll
            starting = self.state.starting_bankroll

        if not history:
            return {"total_trades": 0}

        pnls = [p.pnl for p in history if p.pnl is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0

        return {
            "total_trades": len(history),
            "win_rate": len(wins) / len(pnls) * 100 if pnls else 0,
            "avg_win": sum(wins) / len(wins) if wins else 0,
            "avg_loss": sum(losses) / len(losses) if losses else 0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            "total_pnl": sum(pnls),
            "roi_pct": (bankroll - starting) / starting * 100 if starting > 0 else 0,
            "current_bankroll": bankroll,
            "open_positions": len(self.state.open_positions),
            "consecutive_wins": self.state.consecutive_wins,
            "consecutive_losses": self.state.consecutive_losses,
        }

    def log_status(self) -> None:
        m = self.compute_metrics()
        day_start = self.state.day_start_bankroll
        current = self.state.current_bankroll
        daily_pnl = current - day_start
        daily_pct = (daily_pnl / day_start * 100) if day_start > 0 else 0.0
        daily_tag = "WIN" if daily_pnl >= 0 else "LOSS"
        limit_pct = config.DAILY_LOSS_LIMIT_PCT * 100
        logger.info(
            f"┌─ PORTFOLIO ─────────────────────────────────────\n"
            f"│  Bankroll : ${current:.2f}  "
            f"(peak ${self.state.peak_bankroll:.2f})\n"
            f"│  Today    : [{daily_tag}] ${daily_pnl:+.2f} ({daily_pct:+.1f}%)  "
            f"limit -{limit_pct:.0f}%\n"
            f"│  Trades   : {m.get('total_trades', 0)} total | "
            f"win rate {m.get('win_rate', 0):.1f}%\n"
            f"│  ROI      : {m.get('roi_pct', 0):+.1f}% | "
            f"P&L ${m.get('total_pnl', 0):+.2f}\n"
            f"│  Open pos : {len(self.state.open_positions)} | "
            f"streak W{self.state.consecutive_wins}/L{self.state.consecutive_losses}\n"
            f"└─────────────────────────────────────────────────"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_peaks(self):
        """Must be called inside self._lock."""
        if self.state.current_bankroll > self.state.peak_bankroll:
            self.state.peak_bankroll = self.state.current_bankroll

    def _check_day_reset(self):
        today = utcnow().date().isoformat()
        with self._lock:
            if self.state.day_start_date != today:
                self.state.day_start_date = today
                self.state.day_start_bankroll = self.state.current_bankroll
                logger.info(f"New trading day — bankroll reset baseline: ${self.state.current_bankroll:.2f}")
