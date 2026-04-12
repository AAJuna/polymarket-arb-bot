"""
Portfolio Tracker — single source of truth for financial state.
Thread-safe. Persists to data/portfolio.json. Resumes from crashes.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import config
import requests
from logger_setup import get_logger, TRADE_LEVEL
from utils import utcnow

logger = get_logger(__name__)

DATA_DIR = Path("data")
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
PORTFOLIO_BACKUP_FILE = DATA_DIR / "portfolio.json.bak"
STRATEGY_REPORT_FILE = DATA_DIR / "strategy_expectancy.json"
TRADE_LEDGER_FILE = DATA_DIR / "trade_ledger.jsonl"
_data_api_session = requests.Session()
_data_api_session.headers.update({"Accept": "application/json"})


def _parse_list_field(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _coerce_float(value) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _build_market_url(
    condition_id: str,
    event_slug: str = "",
    market_slug: str = "",
    legacy_slug: str = "",
) -> str:
    if event_slug.endswith("-more-markets"):
        base_event_slug = event_slug[: -len("-more-markets")]
        if not market_slug or market_slug.startswith(base_event_slug):
            event_slug = base_event_slug

    if event_slug and market_slug:
        return f"https://polymarket.com/event/{event_slug}/{market_slug}"
    if legacy_slug:
        return f"https://polymarket.com/event/{legacy_slug}"
    if condition_id:
        return f"https://polymarket.com/predictions?conditionId={condition_id}"
    return "https://polymarket.com/predictions"


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
    action: str = "BUY"         # "BUY"
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    closed_at: Optional[str] = None
    simulated: bool = False
    slug: str = ""
    event_slug: str = ""
    market_slug: str = ""
    market_url: str = ""
    end_date: str = ""
    strategy_type: str = ""
    confidence_source: str = ""
    signal_edge_pct: float = 0.0
    ai_confidence: Optional[float] = None


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
    bankroll_history: list = field(default_factory=list)  # [{timestamp, bankroll}]


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
            if self._load_backup():
                return True
            if self._recover_from_trade_ledger():
                return True
            logger.info("No portfolio file found — starting fresh")
            self._init_fresh()
            return False

        try:
            migrated = self._load_from_path(PORTFOLIO_FILE, source="primary")
            return True
        except Exception as e:
            logger.error(f"Portfolio file corrupted: {e} — attempting recovery")
            if self._load_backup():
                return True
            if self._recover_from_trade_ledger():
                return True
            self._init_fresh()
            return False

    def _load_from_path(self, path: Path, source: str) -> bool:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        with self._lock:
            self.state = PortfolioState(**{
                k: v for k, v in data.items()
                if k in PortfolioState.__dataclass_fields__
            })
        migrated = self._normalize_loaded_positions()
        logger.info(
            f"Portfolio loaded ({source}): bankroll=${self.state.current_bankroll:.2f} "
            f"trades={self.state.total_trades}"
        )
        self.check_day_reset()
        if migrated:
            self.save()
        return True

    def _load_backup(self) -> bool:
        if not PORTFOLIO_BACKUP_FILE.exists():
            return False
        try:
            self._load_from_path(PORTFOLIO_BACKUP_FILE, source="backup")
            logger.warning("Recovered portfolio from backup snapshot")
            return True
        except Exception as exc:
            logger.error(f"Portfolio backup recovery failed: {exc}")
            return False

    def _recover_from_trade_ledger(self) -> bool:
        if not TRADE_LEDGER_FILE.exists():
            return False

        state = PortfolioState()
        recovered = False

        try:
            with open(TRADE_LEDGER_FILE, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    event = json.loads(line)
                    position = event.get("position")
                    if not isinstance(position, dict):
                        continue

                    recovered = True
                    if not state.day_start_date:
                        state.day_start_date = str(
                            event.get("day_start_date") or utcnow().date().isoformat()
                        )
                    state.starting_bankroll = float(
                        event.get("starting_bankroll", state.starting_bankroll or self._starting_bankroll)
                    )
                    state.day_start_bankroll = float(
                        event.get("day_start_bankroll", state.day_start_bankroll or state.starting_bankroll)
                    )
                    state.current_bankroll = float(
                        event.get("current_bankroll_after", state.current_bankroll)
                    )
                    state.peak_bankroll = max(
                        state.peak_bankroll,
                        float(event.get("peak_bankroll_after", state.current_bankroll)),
                    )
                    state.total_trades = int(event.get("total_trades", state.total_trades))
                    state.winning_trades = int(event.get("winning_trades", state.winning_trades))
                    state.consecutive_wins = int(
                        event.get("consecutive_wins", state.consecutive_wins)
                    )
                    state.consecutive_losses = int(
                        event.get("consecutive_losses", state.consecutive_losses)
                    )

                    position_id = str(position.get("position_id", "") or "")
                    if not position_id:
                        continue

                    event_type = str(event.get("event", "") or "").lower()
                    if event_type == "open":
                        state.open_positions[position_id] = position
                    elif event_type == "close":
                        state.open_positions.pop(position_id, None)
                        state.trade_history.append(position)

            if not recovered:
                return False

            with self._lock:
                self.state = state
            logger.warning(
                f"Recovered portfolio from trade ledger: open={len(self.state.open_positions)} "
                f"closed={len(self.state.trade_history)}"
            )
            self.save()
            return True
        except Exception as exc:
            logger.error(f"Trade ledger recovery failed: {exc}")
            return False

    def _normalize_loaded_positions(self) -> bool:
        """Migrate persisted positions so stored trade data remains correct."""
        import scanner as sc

        changed = False
        status_cache: dict[str, dict] = {}

        def get_status(condition_id: str) -> dict:
            if condition_id not in status_cache:
                status_cache[condition_id] = sc.get_market_status(condition_id) if condition_id else {}
            return status_cache[condition_id]

        with self._lock:
            for key, pos in list(self.state.open_positions.items()):
                updated, pos_changed = self._normalize_position_dict(pos, get_status)
                if pos_changed:
                    self.state.open_positions[key] = updated
                    changed = True

            for idx, pos in enumerate(list(self.state.trade_history)):
                updated, pos_changed = self._normalize_position_dict(pos, get_status)
                if pos_changed:
                    self.state.trade_history[idx] = updated
                    changed = True

        return changed

    def _normalize_position_dict(self, pos: dict, get_status) -> tuple[dict, bool]:
        updated = dict(pos)
        changed = False

        condition_id = str(updated.get("condition_id", "") or "")
        token_id = str(updated.get("token_id", "") or "")
        legacy_side = str(updated.get("side", "") or "").upper()
        action = str(updated.get("action", "") or "").upper()

        needs_status = (
            legacy_side not in {"YES", "NO"}
            or not updated.get("event_slug")
            or not updated.get("market_slug")
            or not updated.get("market_url")
        )
        market_status = get_status(condition_id) if needs_status else {}

        if legacy_side in {"BUY", "SELL"}:
            inferred_side = self._infer_outcome_side(token_id, market_status)
            if inferred_side and updated.get("side") != inferred_side:
                updated["side"] = inferred_side
                changed = True
            if not action:
                updated["action"] = legacy_side
                changed = True
        elif not action:
            updated["action"] = "BUY"
            changed = True

        event_slug = str(updated.get("event_slug", "") or "")
        market_slug = str(updated.get("market_slug", "") or "")
        legacy_slug = str(updated.get("slug", "") or "")

        if market_status:
            events = market_status.get("events") or []

            if not event_slug:
                event_slug = str(
                    market_status.get("_event_slug") or
                    (events[0].get("slug", "") if events else "")
                )
                if event_slug:
                    updated["event_slug"] = event_slug
                    changed = True

            if not market_slug:
                market_slug = str(market_status.get("slug", "") or "")
                if market_slug:
                    updated["market_slug"] = market_slug
                    changed = True

            if not legacy_slug and market_slug:
                updated["slug"] = market_slug
                legacy_slug = market_slug
                changed = True

        market_url = _build_market_url(
            condition_id=condition_id,
            event_slug=event_slug,
            market_slug=market_slug,
            legacy_slug=legacy_slug,
        )
        if updated.get("market_url") != market_url:
            updated["market_url"] = market_url
            changed = True

        return updated, changed

    @staticmethod
    def _infer_outcome_side(token_id: str, market_status: dict) -> str:
        token_ids = _parse_list_field(market_status.get("clobTokenIds"))
        if len(token_ids) >= 2:
            if token_id == str(token_ids[0]):
                return "YES"
            if token_id == str(token_ids[1]):
                return "NO"
        return ""

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
        tmp_path = PORTFOLIO_FILE.with_suffix(".json.tmp")
        try:
            DATA_DIR.mkdir(exist_ok=True)
            with self._lock:
                # Append bankroll snapshot
                self.state.bankroll_history.append({
                    "timestamp": utcnow().isoformat(),
                    "bankroll": self.state.current_bankroll,
                })
                # Trim to rolling window
                max_entries = config.BANKROLL_HISTORY_MAX_ENTRIES
                if len(self.state.bankroll_history) > max_entries:
                    self.state.bankroll_history = self.state.bankroll_history[-max_entries:]
                data = asdict(self.state)

            if PORTFOLIO_FILE.exists():
                try:
                    shutil.copy2(PORTFOLIO_FILE, PORTFOLIO_BACKUP_FILE)
                except Exception as exc:
                    logger.warning(f"Failed to refresh portfolio backup: {exc}")

            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, PORTFOLIO_FILE)
            self._write_strategy_report()
            logger.debug("Portfolio saved")
            self._last_save = time.monotonic()
        except Exception as e:
            logger.error(f"Failed to save portfolio: {e}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

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
            action="BUY",
            question=opp.question,
            entry_price=float(order_result.get("fill_price", opp.price)),
            size=float(order_result.get("fill_size", 0)),
            cost_basis=float(order_result.get("fill_cost", 0)),
            opened_at=utcnow().isoformat(),
            status="open",
            order_id=order_result.get("orderID", ""),
            simulated=bool(order_result.get("simulated", False)),
            slug=getattr(opp, "market_slug", "") or getattr(opp, "slug", ""),
            event_slug=getattr(opp, "event_slug", ""),
            market_slug=getattr(opp, "market_slug", ""),
            market_url=getattr(opp, "market_url", ""),
            end_date=opp.end_date.isoformat() if getattr(opp, "end_date", None) else "",
            strategy_type=getattr(opp, "type", ""),
            confidence_source=getattr(opp, "confidence_source", ""),
            signal_edge_pct=float(getattr(opp, "edge_pct", 0.0) or 0.0),
            ai_confidence=(float(getattr(analysis, "confidence", 0.0)) if analysis is not None else None),
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
        self._append_trade_ledger("open", pos)
        self.save()
        return pos

    def close_position(self, position_id: str, payout_per_share: float) -> float:
        """Mark a position as resolved and compute P&L from final payout per share."""
        with self._lock:
            pos_dict = self.state.open_positions.get(position_id)
            if not pos_dict:
                return 0.0

            pos = Position(**pos_dict)
            redemption_value = pos.size * payout_per_share
            pnl = redemption_value - pos.cost_basis

            pos.exit_price = payout_per_share
            pos.pnl = pnl
            pos.closed_at = utcnow().isoformat()
            pos.status = "resolved"

            del self.state.open_positions[position_id]
            self.state.trade_history.append(asdict(pos))

            self.state.current_bankroll += redemption_value
            self.state.total_trades += 1

            if pnl > 0:
                self.state.winning_trades += 1
                self.state.consecutive_wins += 1
                self.state.consecutive_losses = 0
            else:
                self.state.consecutive_losses += 1
                self.state.consecutive_wins = 0

            self._update_peaks()

        if payout_per_share >= 0.99:
            resolution = "WON"
        elif payout_per_share <= 0.01:
            resolution = "LOST"
        else:
            resolution = f"SETTLED @{payout_per_share:.2f}"

        logger.log(
            TRADE_LEVEL,
            f"TRADE CLOSE | {pos.question[:50]} | "
            f"{resolution} | "
            f"pnl=${pnl:+.2f} | bankroll=${self.state.current_bankroll:.2f}"
        )
        self._append_trade_ledger("close", pos)
        self.save()
        return pnl

    def _append_trade_ledger(self, event_type: str, pos: Position) -> None:
        """Append a recovery-friendly trade event with full position data."""
        try:
            DATA_DIR.mkdir(exist_ok=True)
            with self._lock:
                payload = {
                    "event": event_type,
                    "timestamp": utcnow().isoformat(),
                    "position": asdict(pos),
                    "starting_bankroll": self.state.starting_bankroll,
                    "day_start_bankroll": self.state.day_start_bankroll,
                    "day_start_date": self.state.day_start_date,
                    "current_bankroll_after": self.state.current_bankroll,
                    "peak_bankroll_after": self.state.peak_bankroll,
                    "total_trades": self.state.total_trades,
                    "winning_trades": self.state.winning_trades,
                    "consecutive_wins": self.state.consecutive_wins,
                    "consecutive_losses": self.state.consecutive_losses,
                }
            with open(TRADE_LEDGER_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str) + "\n")
        except Exception as exc:
            logger.error(f"Failed to append trade ledger event: {exc}")

    # ------------------------------------------------------------------
    # Resolution checking
    # ------------------------------------------------------------------

    def check_early_exits(self) -> int:
        """Check open paper positions for profitable early exit opportunities.

        If the current market bid price implies a profit of ≥50% of the original
        edge, simulate a sell and close the position early. This captures gains
        without waiting for full market resolution.

        Returns the number of positions exited.
        """
        if not config.PAPER_TRADING:
            return 0

        from realtime_feed import get_shared_feed

        feed = get_shared_feed()
        exited = 0

        with self._lock:
            open_ids = list(self.state.open_positions.keys())

        for pid in open_ids:
            with self._lock:
                pos_dict = self.state.open_positions.get(pid)
            if not pos_dict:
                continue

            token_id = pos_dict.get("token_id", "")
            entry_price = pos_dict.get("entry_price", 0.0)
            edge_pct = pos_dict.get("signal_edge_pct", 0.0)

            if not token_id or entry_price <= 0:
                continue

            # Get current bid price from realtime feed
            current_bid = feed.get_best_bid(token_id) if feed.enabled else None
            if current_bid is None:
                continue

            # Calculate unrealised gain as percentage of entry
            gain_pct = ((current_bid - entry_price) / entry_price) * 100

            # Exit if we've captured ≥50% of the original signal edge,
            # or if we're up ≥15% from entry (whichever is lower threshold)
            min_exit_gain = min(edge_pct * 0.5, 15.0) if edge_pct > 0 else 15.0
            if gain_pct >= max(min_exit_gain, 5.0):
                # Simulate selling at current bid
                self._close_early(pid, current_bid)
                exited += 1

            # Stop-loss: exit if down ≥50% from entry to cut losses
            elif gain_pct <= -50.0:
                self._close_early(pid, current_bid)
                exited += 1

        return exited

    def _close_early(self, position_id: str, sell_price: float) -> float:
        """Close a paper position early at the given sell price."""
        with self._lock:
            pos_dict = self.state.open_positions.get(position_id)
            if not pos_dict:
                return 0.0

            pos = Position(**pos_dict)
            proceeds = pos.size * sell_price
            pnl = proceeds - pos.cost_basis

            pos.exit_price = sell_price
            pos.pnl = pnl
            pos.closed_at = utcnow().isoformat()
            pos.status = "early_exit"

            del self.state.open_positions[position_id]
            self.state.trade_history.append(asdict(pos))

            self.state.current_bankroll += proceeds
            self.state.total_trades += 1

            if pnl > 0:
                self.state.winning_trades += 1
                self.state.consecutive_wins += 1
                self.state.consecutive_losses = 0
            else:
                self.state.consecutive_losses += 1
                self.state.consecutive_wins = 0

            self._update_peaks()

        action = "PROFIT-TAKE" if pnl > 0 else "STOP-LOSS"
        logger.log(
            TRADE_LEVEL,
            f"TRADE EARLY EXIT ({action}) | {pos.question[:50]} | "
            f"sell@${sell_price:.3f} | pnl=${pnl:+.2f} | "
            f"bankroll=${self.state.current_bankroll:.2f}"
        )
        self._append_trade_ledger("early_exit", pos)
        self.save()
        return pnl

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

                closed = bool(market_status.get("closed"))
                outcome_prices_raw = _parse_list_field(market_status.get("outcomePrices"))
                outcome_prices = []
                for value in outcome_prices_raw:
                    try:
                        outcome_prices.append(float(value))
                    except Exception:
                        outcome_prices.append(0.0)

                resolved = bool(market_status.get("resolved"))
                if not resolved and closed and len(outcome_prices) >= 2:
                    yes_payout = outcome_prices[0]
                    no_payout = outcome_prices[1]
                    resolved = (
                        yes_payout >= 0.99
                        or no_payout >= 0.99
                        or (abs(yes_payout - 0.5) <= 0.01 and abs(no_payout - 0.5) <= 0.01)
                    )

                if resolved and closed and len(outcome_prices) >= 2:
                    payout_per_share = outcome_prices[0] if pos.side == "YES" else outcome_prices[1]
                    self.close_position(pid, payout_per_share)
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

    def _request_data_api(self, path_candidates: list[str], user_address: str):
        params = {"user": user_address}
        last_error = None
        for path in path_candidates:
            try:
                resp = _data_api_session.get(
                    f"{config.DATA_API_HOST}{path}",
                    params=params,
                    timeout=10,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_error = e
        if last_error:
            raise last_error
        return None

    @staticmethod
    def _extract_positions(payload) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("data", "positions", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_total_value(payload) -> Optional[float]:
        if payload is None:
            return None
        direct = _coerce_float(payload)
        if direct is not None:
            return direct
        if isinstance(payload, dict):
            for key in ("value", "totalValue", "total_value", "currentValue", "positionValue"):
                value = _coerce_float(payload.get(key))
                if value is not None:
                    return value
            nested = payload.get("data")
            if nested is not None:
                return Portfolio._extract_total_value(nested)
        return None

    def reconcile_live_account(self, user_address: str) -> Optional[dict]:
        """Best-effort Data API snapshot for live trading sanity checks."""
        if config.PAPER_TRADING or not user_address:
            return None

        try:
            positions_payload = self._request_data_api(
                ["/positions", "/v1/positions"],
                user_address,
            )
            value_payload = self._request_data_api(
                ["/value", "/v1/value"],
                user_address,
            )
        except Exception as e:
            logger.warning(f"Data API reconciliation failed for {user_address[:10]}...: {e}")
            return None

        positions = self._extract_positions(positions_payload)
        total_value = self._extract_total_value(value_payload)
        live_open = len(
            [
                pos for pos in positions
                if (_coerce_float(pos.get('size')) or 0.0) > 0
            ]
        )

        with self._lock:
            local_open = len(self.state.open_positions)

        if live_open != local_open:
            logger.warning(
                f"Data API mismatch â€” local_open={local_open} vs live_open={live_open}"
            )

        if total_value is not None:
            logger.info(
                f"Data API snapshot â€” live_open={live_open} | "
                f"position_value=${total_value:.2f}"
            )

        return {
            "live_open": live_open,
            "position_value": total_value,
        }

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _strategy_metrics(positions: list[Position]) -> dict:
        pnls = [p.pnl for p in positions if p.pnl is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        avg_edge = (
            sum(p.signal_edge_pct for p in positions) / len(positions)
            if positions else 0.0
        )
        avg_ai_conf = (
            sum(p.ai_confidence for p in positions if p.ai_confidence is not None)
            / max(1, len([p for p in positions if p.ai_confidence is not None]))
            if positions else 0.0
        )
        return {
            "trades": len(positions),
            "resolved_trades": len(pnls),
            "win_rate": len(wins) / len(pnls) * 100 if pnls else 0.0,
            "total_pnl": sum(pnls),
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "avg_edge_pct": avg_edge,
            "avg_ai_confidence": avg_ai_conf,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        }

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

        by_strategy_positions: dict[str, list[Position]] = defaultdict(list)
        for pos in history:
            by_strategy_positions[pos.strategy_type or "unknown"].append(pos)

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
            "by_strategy": {
                strategy: self._strategy_metrics(positions)
                for strategy, positions in sorted(by_strategy_positions.items())
            },
        }

    def _write_strategy_report(self) -> None:
        metrics = self.compute_metrics()
        report = {
            "updated_at": utcnow().isoformat(),
            "mode": "paper" if config.PAPER_TRADING else "live",
            "total_trades": metrics.get("total_trades", 0),
            "overall": {
                "win_rate": metrics.get("win_rate", 0.0),
                "total_pnl": metrics.get("total_pnl", 0.0),
                "profit_factor": metrics.get("profit_factor", 0.0),
                "roi_pct": metrics.get("roi_pct", 0.0),
            },
            "by_strategy": metrics.get("by_strategy", {}),
        }
        with open(STRATEGY_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

    def log_status(self) -> None:
        m = self.compute_metrics()
        day_start = self.state.day_start_bankroll
        current = self.state.current_bankroll
        open_cost = sum(p.get("cost_basis", 0) for p in self.state.open_positions.values())
        realized_bankroll = current + open_cost
        daily_pnl = realized_bankroll - day_start
        daily_pct = (daily_pnl / day_start * 100) if day_start > 0 else 0.0
        daily_icon = "+" if daily_pnl >= 0 else "-"
        total_pnl = m.get('total_pnl', 0)
        win_rate = m.get('win_rate', 0)
        total_trades = m.get('total_trades', 0)
        roi = m.get('roi_pct', 0)
        n_open = len(self.state.open_positions)
        wins = self.state.consecutive_wins
        losses = self.state.consecutive_losses

        logger.info(
            f"+-- PORTFOLIO ------------------------------------\n"
            f"|  Bankroll   ${current:>10.2f}    peak ${self.state.peak_bankroll:.2f}\n"
            f"|  Today      ${daily_pnl:>+10.2f}  ({daily_pct:+.1f}%)\n"
            f"|  All-time   ${total_pnl:>+10.2f}  ({roi:+.1f}% ROI)\n"
            f"|  Trades     {total_trades:>10}    win rate {win_rate:.0f}%\n"
            f"|  Open       {n_open:>10}    streak W{wins}/L{losses}\n"
            f"+------------------------------------------------"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_peaks(self):
        """Must be called inside self._lock."""
        if self.state.current_bankroll > self.state.peak_bankroll:
            self.state.peak_bankroll = self.state.current_bankroll

    def check_day_reset(self):
        today = utcnow().date().isoformat()
        with self._lock:
            if self.state.day_start_date != today:
                self.state.day_start_date = today
                open_cost = sum(p.get("cost_basis", 0) for p in self.state.open_positions.values())
                equity = self.state.current_bankroll + open_cost
                self.state.day_start_bankroll = equity
                logger.info(f"New trading day — equity reset baseline: ${equity:.2f}")

    def _check_day_reset(self):
        today = utcnow().date().isoformat()
        with self._lock:
            if self.state.day_start_date != today:
                self.state.day_start_date = today
                self.state.day_start_bankroll = self.state.current_bankroll
                logger.info(f"New trading day — bankroll reset baseline: ${self.state.current_bankroll:.2f}")
