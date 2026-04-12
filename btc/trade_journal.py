"""
BTC Trade Journal — logs detailed trade data for strategy evaluation.

Saves every trade with full context (AI decision, prices, timing) to
data/btc/trade_journal.jsonl. After every 50 trades, runs an
auto-review and logs strategy performance breakdown.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from logger_setup import get_logger

logger = get_logger(__name__)

JOURNAL_FILE = Path("data/btc/trade_journal.jsonl")
REVIEW_FILE = Path("data/btc/strategy_review.json")
REVIEW_INTERVAL = 50  # evaluate every N trades


def log_trade(
    market_id: str,
    side: str,
    entry_price: float,
    cost: float,
    confidence: float,
    strategy: str,
    reasoning: str,
    btc_price_at_entry: float,
    up_price: float,
    down_price: float,
    window_start: str = "",
    window_end: str = "",
) -> None:
    """Append a trade entry to the journal."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "open",
        "market_id": market_id,
        "side": side,
        "entry_price": entry_price,
        "cost": round(cost, 4),
        "confidence": round(confidence, 3),
        "strategy": strategy,
        "reasoning": reasoning[:200],
        "btc_price": round(btc_price_at_entry, 2),
        "up_price": up_price,
        "down_price": down_price,
        "window_start": window_start,
        "window_end": window_end,
    }
    _append(entry)


def log_result(
    market_id: str,
    pnl: float,
    exit_price: float,
    status: str,
    strategy: str,
) -> None:
    """Append a trade result to the journal."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "close",
        "market_id": market_id,
        "pnl": round(pnl, 4),
        "exit_price": exit_price,
        "status": status,
        "strategy": strategy,
    }
    _append(entry)

    # Check if we should run auto-review
    total = _count_closed()
    if total > 0 and total % REVIEW_INTERVAL == 0:
        run_review()


def run_review() -> dict:
    """Analyze all closed trades and produce strategy breakdown."""
    trades = _load_all()

    # Pair opens with closes by market_id
    opens = {}
    results = []
    for t in trades:
        if t.get("event") == "open":
            opens[t["market_id"]] = t
        elif t.get("event") == "close":
            mid = t["market_id"]
            open_data = opens.get(mid, {})
            results.append({
                "market_id": mid,
                "strategy": open_data.get("strategy", t.get("strategy", "")),
                "side": open_data.get("side", ""),
                "confidence": open_data.get("confidence", 0),
                "cost": open_data.get("cost", 0),
                "pnl": t.get("pnl", 0),
                "status": t.get("status", ""),
            })

    if not results:
        return {}

    # Overall stats
    total = len(results)
    wins = sum(1 for r in results if r["pnl"] > 0)
    losses = total - wins
    total_pnl = sum(r["pnl"] for r in results)
    avg_win = sum(r["pnl"] for r in results if r["pnl"] > 0) / max(1, wins)
    avg_loss = sum(r["pnl"] for r in results if r["pnl"] <= 0) / max(1, losses)

    # Per-strategy breakdown
    strategies = {}
    for r in results:
        s = r["strategy"] or "unknown"
        if s not in strategies:
            strategies[s] = {"trades": 0, "wins": 0, "pnl": 0.0, "costs": 0.0}
        strategies[s]["trades"] += 1
        strategies[s]["pnl"] += r["pnl"]
        strategies[s]["costs"] += r["cost"]
        if r["pnl"] > 0:
            strategies[s]["wins"] += 1

    for s, d in strategies.items():
        d["win_rate"] = round(d["wins"] / max(1, d["trades"]) * 100, 1)
        d["avg_pnl"] = round(d["pnl"] / max(1, d["trades"]), 4)
        d["roi"] = round(d["pnl"] / max(0.01, d["costs"]) * 100, 1)
        d["pnl"] = round(d["pnl"], 4)
        d["costs"] = round(d["costs"], 4)

    # Confidence bracket analysis
    conf_brackets = {"low (55-60%)": [], "mid (60-70%)": [], "high (70%+)": []}
    for r in results:
        c = r["confidence"]
        if c >= 0.70:
            conf_brackets["high (70%+)"].append(r["pnl"])
        elif c >= 0.60:
            conf_brackets["mid (60-70%)"].append(r["pnl"])
        else:
            conf_brackets["low (55-60%)"].append(r["pnl"])

    conf_stats = {}
    for bracket, pnls in conf_brackets.items():
        if pnls:
            w = sum(1 for p in pnls if p > 0)
            conf_stats[bracket] = {
                "trades": len(pnls),
                "win_rate": round(w / len(pnls) * 100, 1),
                "total_pnl": round(sum(pnls), 4),
            }

    review = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 1),
        "total_pnl": round(total_pnl, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "strategies": strategies,
        "confidence_brackets": conf_stats,
    }

    # Save review
    try:
        REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_FILE, "w", encoding="utf-8") as f:
            json.dump(review, f, indent=2)
    except Exception:
        pass

    # Log summary
    logger.info(f"{'='*50}")
    logger.info(f"STRATEGY REVIEW — {total} trades")
    logger.info(f"  Overall: {wins}W/{losses}L ({review['win_rate']}%) P&L=${total_pnl:+.2f}")
    logger.info(f"  Avg win: ${avg_win:+.2f}  Avg loss: ${avg_loss:+.2f}")
    for s, d in strategies.items():
        logger.info(f"  [{s}] {d['trades']}t {d['win_rate']}% wr P&L=${d['pnl']:+.2f} ROI={d['roi']}%")
    logger.info(f"{'='*50}")

    return review


def _append(entry: dict) -> None:
    try:
        JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _load_all() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    entries = []
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception:
        pass
    return entries


def _count_closed() -> int:
    return sum(1 for t in _load_all() if t.get("event") == "close")
