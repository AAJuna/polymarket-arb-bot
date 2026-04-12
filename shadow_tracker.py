"""
Shadow signal tracker.

Persists detector signals that were not necessarily executed, then resolves them
later using official market outcomes to estimate strategy expectancy per $1
notional.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import config
from logger_setup import get_logger
from utils import parse_iso, seconds_until, utcnow

logger = get_logger(__name__)

DATA_DIR = Path("data")
SHADOW_SIGNALS_FILE = DATA_DIR / "shadow_signals.json"
SHADOW_REPORT_FILE = DATA_DIR / "shadow_report.json"


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


@dataclass
class ShadowSignal:
    signal_id: str
    strategy_type: str
    market_id: str
    condition_id: str
    token_id: str
    side: str
    question: str
    entry_price: float
    first_edge_pct: float
    max_edge_pct: float
    last_edge_pct: float
    confidence_source: str
    detected_at: str
    last_seen_at: str
    end_date: str = ""
    market_url: str = ""
    status: str = "open"  # open | resolved
    seen_count: int = 1
    match_confidence: Optional[float] = None
    payout_per_share: Optional[float] = None
    pnl_per_dollar: Optional[float] = None
    resolved_at: Optional[str] = None


class ShadowTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._signals: dict[str, ShadowSignal] = {}
        self._last_save = time.monotonic()
        DATA_DIR.mkdir(exist_ok=True)
        self.load()

    def load(self) -> bool:
        if not SHADOW_SIGNALS_FILE.exists():
            return False
        try:
            with open(SHADOW_SIGNALS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            items = raw if isinstance(raw, list) else raw.get("signals", [])
            with self._lock:
                self._signals = {
                    item["signal_id"]: ShadowSignal(**item)
                    for item in items
                    if isinstance(item, dict) and item.get("signal_id")
                }
            return True
        except Exception as exc:
            logger.warning(f"Failed to load shadow signals: {exc}")
            return False

    def save(self) -> None:
        with self._lock:
            payload = [
                asdict(signal)
                for signal in sorted(
                    self._signals.values(),
                    key=lambda item: (item.status, item.detected_at, item.signal_id),
                )
            ]
        with open(SHADOW_SIGNALS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        self._write_report()
        self._last_save = time.monotonic()

    def maybe_save(self) -> None:
        if time.monotonic() - self._last_save >= config.PORTFOLIO_SAVE_INTERVAL:
            self.save()

    @staticmethod
    def _signal_id(opp) -> str:
        return f"{getattr(opp, 'type', '')}:{getattr(opp, 'token_id', '')}:{getattr(opp, 'side', '')}"

    def track_opportunities(self, opportunities: list) -> int:
        created = 0
        now = utcnow().isoformat()
        with self._lock:
            for opp in opportunities:
                if getattr(opp, "type", "") == "same_market":
                    continue

                signal_id = self._signal_id(opp)
                ext = getattr(opp, "external_odds", None)
                existing = self._signals.get(signal_id)
                if existing is None:
                    self._signals[signal_id] = ShadowSignal(
                        signal_id=signal_id,
                        strategy_type=str(getattr(opp, "type", "") or ""),
                        market_id=str(getattr(opp, "market_id", "") or ""),
                        condition_id=str(getattr(opp, "condition_id", "") or ""),
                        token_id=str(getattr(opp, "token_id", "") or ""),
                        side=str(getattr(opp, "side", "") or ""),
                        question=str(getattr(opp, "question", "") or ""),
                        entry_price=float(getattr(opp, "price", 0.0) or 0.0),
                        first_edge_pct=float(getattr(opp, "edge_pct", 0.0) or 0.0),
                        max_edge_pct=float(getattr(opp, "edge_pct", 0.0) or 0.0),
                        last_edge_pct=float(getattr(opp, "edge_pct", 0.0) or 0.0),
                        confidence_source=str(getattr(opp, "confidence_source", "") or ""),
                        detected_at=now,
                        last_seen_at=now,
                        end_date=(getattr(opp, "end_date", None).isoformat() if getattr(opp, "end_date", None) else ""),
                        market_url=str(getattr(opp, "market_url", "") or ""),
                        match_confidence=(float(ext.match_confidence) if ext is not None else None),
                    )
                    created += 1
                    continue

                existing.last_seen_at = now
                existing.seen_count += 1
                existing.last_edge_pct = float(getattr(opp, "edge_pct", 0.0) or 0.0)
                existing.max_edge_pct = max(existing.max_edge_pct, existing.last_edge_pct)
                if ext is not None and ext.match_confidence is not None:
                    existing.match_confidence = float(ext.match_confidence)
        return created

    def resolve_signals(self) -> int:
        import scanner as sc

        resolved_count = 0
        with self._lock:
            candidates = [
                signal for signal in self._signals.values()
                if signal.status == "open"
            ]

        for signal in candidates:
            if signal.end_date:
                try:
                    if seconds_until(parse_iso(signal.end_date)) > 0:
                        continue
                except Exception:
                    pass

            try:
                market_status = sc.get_market_status(signal.condition_id)
                if not market_status:
                    continue

                closed = bool(market_status.get("closed"))
                outcome_prices = []
                for value in _parse_list_field(market_status.get("outcomePrices")):
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

                if not (resolved and closed and len(outcome_prices) >= 2):
                    continue

                payout_per_share = outcome_prices[0] if signal.side == "YES" else outcome_prices[1]
                entry_price = max(signal.entry_price, 1e-9)
                shares = 1.0 / entry_price
                pnl_per_dollar = shares * payout_per_share - 1.0

                with self._lock:
                    current = self._signals.get(signal.signal_id)
                    if current is None or current.status != "open":
                        continue
                    current.status = "resolved"
                    current.payout_per_share = payout_per_share
                    current.pnl_per_dollar = pnl_per_dollar
                    current.resolved_at = utcnow().isoformat()
                resolved_count += 1
            except Exception as exc:
                logger.debug(f"Shadow resolution failed for {signal.signal_id}: {exc}")

        return resolved_count

    @staticmethod
    def _bucket_label(price: float) -> str:
        if price < 0.15:
            return "<$0.15"
        if price < 0.30:
            return "$0.15-$0.30"
        if price < 0.50:
            return "$0.30-$0.50"
        return "$0.50+"

    @staticmethod
    def _bucket_summary(signals: list[ShadowSignal]) -> dict:
        resolved = [s for s in signals if s.pnl_per_dollar is not None]
        wins = [s for s in resolved if (s.pnl_per_dollar or 0.0) > 0]
        return {
            "signals": len(signals),
            "resolved": len(resolved),
            "win_rate": len(wins) / len(resolved) * 100 if resolved else 0.0,
            "avg_pnl": (
                sum(s.pnl_per_dollar or 0.0 for s in resolved) / len(resolved)
                if resolved else 0.0
            ),
        }

    @staticmethod
    def _strategy_summary(signals: list[ShadowSignal]) -> dict:
        resolved = [signal for signal in signals if signal.pnl_per_dollar is not None]
        wins = [signal for signal in resolved if (signal.pnl_per_dollar or 0.0) > 0]

        # Price bucket breakdown
        by_bucket: dict[str, list[ShadowSignal]] = defaultdict(list)
        for signal in signals:
            bucket = ShadowTracker._bucket_label(signal.entry_price)
            by_bucket[bucket].append(signal)

        return {
            "signals": len(signals),
            "resolved_signals": len(resolved),
            "open_signals": len(signals) - len(resolved),
            "win_rate": len(wins) / len(resolved) * 100 if resolved else 0.0,
            "avg_pnl_per_dollar": (
                sum(signal.pnl_per_dollar or 0.0 for signal in resolved) / len(resolved)
                if resolved else 0.0
            ),
            "avg_first_edge_pct": (
                sum(signal.first_edge_pct for signal in signals) / len(signals)
                if signals else 0.0
            ),
            "avg_max_edge_pct": (
                sum(signal.max_edge_pct for signal in signals) / len(signals)
                if signals else 0.0
            ),
            "by_price_bucket": {
                bucket: ShadowTracker._bucket_summary(items)
                for bucket, items in sorted(by_bucket.items())
            },
        }

    def build_report(self) -> dict:
        with self._lock:
            signals = list(self._signals.values())

        by_strategy: dict[str, list[ShadowSignal]] = defaultdict(list)
        for signal in signals:
            by_strategy[signal.strategy_type or "unknown"].append(signal)

        resolved = [signal for signal in signals if signal.pnl_per_dollar is not None]
        report = {
            "updated_at": utcnow().isoformat(),
            "mode": "paper" if config.PAPER_TRADING else "live",
            "overview": {
                "signals": len(signals),
                "resolved_signals": len(resolved),
                "open_signals": len(signals) - len(resolved),
            },
            "by_strategy": {
                strategy: self._strategy_summary(items)
                for strategy, items in sorted(by_strategy.items())
            },
            "recent_resolved": [
                {
                    "strategy_type": signal.strategy_type,
                    "question": signal.question,
                    "pnl_per_dollar": signal.pnl_per_dollar,
                    "resolved_at": signal.resolved_at,
                    "market_url": signal.market_url,
                }
                for signal in sorted(
                    resolved,
                    key=lambda item: item.resolved_at or "",
                    reverse=True,
                )[:10]
            ],
        }
        return report

    def _write_report(self) -> None:
        report = self.build_report()
        with open(SHADOW_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

    def log_summary(self) -> None:
        report = self.build_report()
        overview = report.get("overview", {})
        total = overview.get('signals', 0)
        resolved = overview.get('resolved_signals', 0)
        open_count = overview.get('open_signals', 0)

        lines = [f"Shadow signals: {total} total, {resolved} resolved, {open_count} open"]

        by_strategy = report.get("by_strategy", {})
        for strategy in sorted(by_strategy):
            s = by_strategy[strategy]
            n = s.get('signals', 0)
            exp = s.get('avg_pnl_per_dollar', 0.0)
            wr = s.get('win_rate', 0.0)
            res = s.get('resolved_signals', 0)
            line = f"  {strategy}: {n} signals"
            if res > 0:
                line += f"  win={wr:.0f}%  exp=${exp:+.3f}/$1"

            # Price bucket breakdown
            buckets = s.get('by_price_bucket', {})
            if buckets:
                bucket_parts = []
                for bucket, bd in sorted(buckets.items()):
                    bn = bd.get('signals', 0)
                    br = bd.get('resolved', 0)
                    bw = bd.get('win_rate', 0.0)
                    bp = bd.get('avg_pnl', 0.0)
                    part = f"{bucket}:{bn}"
                    if br > 0:
                        part += f"({bw:.0f}%)"
                    bucket_parts.append(part)
                line += f"  [{' '.join(bucket_parts)}]"
            lines.append(line)

        logger.info("\n".join(lines))
