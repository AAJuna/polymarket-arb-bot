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
LEDGER_FILE = Path("data/btc/trade_ledger.jsonl")
REVIEW_FILE = Path("data/btc/strategy_review.json")
REVIEW_HISTORY_FILE = Path("data/btc/review_history.jsonl")
REVIEW_INTERVAL = 50  # evaluate every N trades


def _snapshot_config() -> dict:
    """Snapshot current BTC config for review comparison."""
    try:
        from btc import config_btc as cfg
        return {
            "blocked_strategies": list(getattr(cfg, "BLOCKED_STRATEGIES", [])),
            "max_ai_confidence": getattr(cfg, "MAX_AI_CONFIDENCE", None),
            "min_confidence": getattr(cfg, "MIN_CONFIDENCE", None),
            "bet_confidence_scale": getattr(cfg, "BET_CONFIDENCE_SCALE", None),
            "consecutive_loss_pause": getattr(cfg, "CONSECUTIVE_LOSS_PAUSE", None),
            "consecutive_loss_reduce": getattr(cfg, "CONSECUTIVE_LOSS_REDUCE", None),
            "pause_duration_min": getattr(cfg, "PAUSE_DURATION_MINUTES", None),
            "max_concurrent_windows": getattr(cfg, "MAX_CONCURRENT_WINDOWS", None),
            "max_entry_price": getattr(cfg, "MAX_ENTRY_PRICE", None),
            "bet_size_pct": getattr(cfg, "BET_SIZE_PCT", None),
        }
    except Exception:
        return {}


def _load_last_review() -> Optional[dict]:
    """Return last review from history, or None if empty/missing."""
    if not REVIEW_HISTORY_FILE.exists():
        return None
    try:
        with open(REVIEW_HISTORY_FILE, "r", encoding="utf-8") as f:
            last = None
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except Exception:
                    continue
            return last
    except Exception:
        return None


def _append_history(entry: dict) -> None:
    """Append a review entry to review_history.jsonl."""
    try:
        REVIEW_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Failed to append review history: {e}")


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
    """Analyze all closed trades using Claude Opus for deep evaluation.

    Reads from trade_ledger.jsonl (complete data from portfolio) instead
    of trade_journal.jsonl (which may be incomplete). Computes stats,
    then sends everything to Opus for strategic recommendations.
    """
    trades = _load_ledger()

    # Pair opens with closes by position_id
    opens = {}
    results = []
    for t in trades:
        pos = t.get("position", {})
        pid = pos.get("position_id", "")
        if t.get("event") == "open":
            opens[pid] = t
        elif t.get("event") == "close":
            open_data = opens.get(pid, {})
            open_pos = open_data.get("position", {})
            results.append({
                "market_id": pos.get("market_id", ""),
                "strategy": pos.get("confidence_source", open_pos.get("confidence_source", "")),
                "side": pos.get("side", open_pos.get("side", "")),
                "confidence": pos.get("ai_confidence") or open_pos.get("ai_confidence") or 0,
                "cost": pos.get("cost_basis", open_pos.get("cost_basis", 0)),
                "pnl": pos.get("pnl", 0) or 0,
                "status": pos.get("status", ""),
                "reasoning": "",
                "btc_price": 0,
                "up_price": open_pos.get("entry_price", 0),
                "down_price": 0,
                "question": pos.get("question", open_pos.get("question", "")),
                "entry_price": pos.get("entry_price", open_pos.get("entry_price", 0)),
                "exit_price": pos.get("exit_price", 0) or 0,
                "edge_pct": pos.get("signal_edge_pct", open_pos.get("signal_edge_pct", 0)),
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

    # Log basic stats
    logger.info(f"{'='*50}")
    logger.info(f"STRATEGY REVIEW — {total} trades")
    logger.info(f"  Overall: {wins}W/{losses}L ({review['win_rate']}%) P&L=${total_pnl:+.2f}")
    for s, d in strategies.items():
        logger.info(f"  [{s}] {d['trades']}t {d['win_rate']}% wr P&L=${d['pnl']:+.2f} ROI={d['roi']}%")

    # Load previous review for compliance check
    prev_review = _load_last_review()
    current_config = _snapshot_config()

    # Compute stats for trades added since last review
    post_review_stats = None
    if prev_review:
        prev_total = prev_review.get("total_trades_at_review", 0)
        new_trades = results[prev_total:]
        if new_trades:
            n_new = len(new_trades)
            n_wins = sum(1 for r in new_trades if r["pnl"] > 0)
            n_pnl = sum(r["pnl"] for r in new_trades)
            post_strats: dict = {}
            for r in new_trades:
                s = r["strategy"] or "unknown"
                if s not in post_strats:
                    post_strats[s] = {"trades": 0, "wins": 0, "pnl": 0.0}
                post_strats[s]["trades"] += 1
                post_strats[s]["pnl"] += r["pnl"]
                if r["pnl"] > 0:
                    post_strats[s]["wins"] += 1
            for s, d in post_strats.items():
                d["win_rate"] = round(d["wins"] / max(1, d["trades"]) * 100, 1)
                d["pnl"] = round(d["pnl"], 4)
            post_review_stats = {
                "trades": n_new,
                "wins": n_wins,
                "losses": n_new - n_wins,
                "win_rate": round(n_wins / max(1, n_new) * 100, 1),
                "total_pnl": round(n_pnl, 4),
                "strategies": post_strats,
            }

    # Send to Claude Opus for deep analysis
    opus_analysis = _opus_review(results, review, prev_review, current_config, post_review_stats)
    if opus_analysis:
        review["opus_analysis"] = opus_analysis
        logger.info(f"OPUS EVALUATION:")
        for line in opus_analysis.split("\n"):
            if line.strip():
                logger.info(f"  {line.strip()}")

    logger.info(f"{'='*50}")

    # Save review
    try:
        REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_FILE, "w", encoding="utf-8") as f:
            json.dump(review, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    # Append to history (always, even if Opus failed)
    _append_history({
        "timestamp": review["reviewed_at"],
        "total_trades_at_review": total,
        "win_rate": review["win_rate"],
        "total_pnl": review["total_pnl"],
        "opus_analysis": opus_analysis or "",
        "config_snapshot": current_config,
    })

    return review


def _opus_review(
    results: list[dict],
    stats: dict,
    prev_review: Optional[dict] = None,
    current_config: Optional[dict] = None,
    post_review_stats: Optional[dict] = None,
) -> Optional[str]:
    """Send trade data to Claude Opus for strategic evaluation."""
    try:
        import os
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("Opus review skipped: ANTHROPIC_API_KEY not set")
            return None

        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
    except Exception as e:
        logger.warning(f"Opus review init failed: {e}")
        return None

    # Build trade summary for Opus
    trade_lines = []
    for r in results[-50:]:
        wl = "WIN" if r["pnl"] > 0 else "LOSS"
        conf_str = f"{r['confidence']:.0%}" if r['confidence'] else "n/a"
        trade_lines.append(
            f"  {r['market_id']} | {r['side']} | {r['strategy']} | "
            f"conf={conf_str} | entry={r.get('entry_price', 0):.2f} | "
            f"cost=${r['cost']:.2f} | pnl=${r['pnl']:+.2f} | {wl}"
        )
    trades_str = "\n".join(trade_lines)

    strat_lines = []
    for s, d in stats.get("strategies", {}).items():
        strat_lines.append(
            f"  {s}: {d['trades']} trades, {d['win_rate']}% win rate, "
            f"P&L=${d['pnl']:+.2f}, ROI={d['roi']}%"
        )
    strat_str = "\n".join(strat_lines)

    # Build compliance section if prev_review exists
    compliance_section = ""
    compliance_instruction = ""
    if prev_review and prev_review.get("opus_analysis"):
        prev_ts = prev_review.get("timestamp", "")
        prev_total = prev_review.get("total_trades_at_review", 0)
        prev_config = prev_review.get("config_snapshot", {})
        prev_analysis = prev_review.get("opus_analysis", "")
        post_stats_json = (
            json.dumps(post_review_stats, indent=2) if post_review_stats
            else "No trades since last review"
        )
        compliance_section = f"""
PREVIOUS REVIEW ({prev_ts}, {prev_total} trades at that time):
{prev_analysis}

CONFIG AT PREVIOUS REVIEW:
{json.dumps(prev_config, indent=2)}

CURRENT CONFIG:
{json.dumps(current_config or {}, indent=2)}

TRADES SINCE PREVIOUS REVIEW:
{post_stats_json}
"""
        compliance_instruction = """
FIRST, output a section titled "# PREVIOUS RECOMMENDATIONS STATUS".
For EACH actionable recommendation from the previous review, mark it:
- ✅ APPLIED — current config/behavior matches the recommendation
- ❌ NOT APPLIED — recommendation was made but config/behavior unchanged
- ⚠️ PARTIAL — some aspects applied, others not

Include specific evidence (config values, trade stats) for each verdict.
Then briefly evaluate whether the applied changes improved performance
(compare trades-since-review stats to overall stats).

THEN continue with the normal evaluation below.
"""

    prompt = f"""You are evaluating a BTC 5-minute prediction bot on Polymarket.
The bot uses Claude Haiku to analyze 60 seconds of BTC price data, then decides UP or DOWN.
{compliance_section}
PERFORMANCE SUMMARY (all trades):
- Total trades: {stats['total_trades']}
- Win rate: {stats['win_rate']}%
- Total P&L: ${stats['total_pnl']:+.2f}
- Avg win: ${stats['avg_win']:+.2f}, Avg loss: ${stats['avg_loss']:+.2f}

STRATEGY BREAKDOWN:
{strat_str}

CONFIDENCE BRACKETS:
{json.dumps(stats.get('confidence_brackets', {}), indent=2)}

RECENT TRADES (last 50):
{trades_str}
{compliance_instruction}
EVALUATE:
1. Which strategy performs best and why?
2. Should the bot favor one strategy over another?
3. Is the confidence calibrated well? (Do high-confidence trades actually win more?)
4. What patterns do you see in the losses?
5. Specific recommendations to improve win rate and P&L.
6. Should position sizing change based on the data?

Be concise and actionable. Focus on what to CHANGE, not what's working fine."""

    try:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1600,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        logger.warning(f"Opus review failed: {e}")
        return None


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


def _load_ledger() -> list[dict]:
    """Load from trade_ledger.jsonl (complete data from portfolio)."""
    if not LEDGER_FILE.exists():
        return _load_all()  # fallback to journal
    entries = []
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception:
        pass
    return entries


def _count_closed() -> int:
    """Count closed trades from ledger (complete) or journal (fallback)."""
    source = _load_ledger()
    return sum(1 for t in source if t.get("event") == "close")
