"""
Polymarket Bot — Streamlit Dashboard
Auto-refreshes every 10 seconds.
Run: streamlit run dashboard.py
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

import config

PORTFOLIO_FILE = Path("data/portfolio.json")
REFRESH_SECONDS = 10

st.set_page_config(
    page_title="Polymarket Bot",
    page_icon="🤖",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_portfolio() -> dict | None:
    if not PORTFOLIO_FILE.exists():
        return None
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def fmt_pct(value: float, decimals: int = 1) -> str:
    return f"{value:+.{decimals}f}%"


def fmt_usd(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}${value:.2f}"


def is_paused(data: dict) -> bool:
    pause_until = data.get("pause_until")
    if not pause_until:
        return False
    try:
        until = datetime.fromisoformat(pause_until)
        return datetime.now(timezone.utc) < until
    except Exception:
        return False


def daily_loss_pct(data: dict) -> float:
    day_start = data.get("day_start_bankroll", 0)
    current = data.get("current_bankroll", 0)
    if day_start <= 0:
        return 0.0
    return (day_start - current) / day_start * 100  # positive = loss


def drawdown_pct(data: dict) -> float:
    peak = data.get("peak_bankroll", 0)
    current = data.get("current_bankroll", 0)
    if peak <= 0:
        return 0.0
    return (peak - current) / peak * 100  # positive = drawdown


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

# Header
col_title, col_mode, col_time = st.columns([4, 1, 2])
with col_title:
    st.title("🤖 Polymarket Bot Dashboard")
with col_mode:
    mode = "🟡 PAPER" if config.PAPER_TRADING else "🟢 LIVE"
    st.metric("Mode", mode)
with col_time:
    st.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

st.divider()

data = load_portfolio()

if not data:
    st.error("Portfolio file not found — is the bot running?")
    time.sleep(REFRESH_SECONDS)
    st.rerun()

# ---------------------------------------------------------------------------
# Bot status banner
# ---------------------------------------------------------------------------

current = data.get("current_bankroll", 0)
day_start = data.get("day_start_bankroll", 0)
peak = data.get("peak_bankroll", 0)
starting = data.get("starting_bankroll", 0)
open_pos = data.get("open_positions", {})
history = data.get("trade_history", [])
cons_wins = data.get("consecutive_wins", 0)
cons_losses = data.get("consecutive_losses", 0)

daily_loss = daily_loss_pct(data)
dd = drawdown_pct(data)
limit_pct = config.DAILY_LOSS_LIMIT_PCT * 100
paused = is_paused(data)

daily_pnl_usd = current - day_start
total_pnl_usd = current - starting

blocked_daily = daily_loss >= limit_pct
blocked_drawdown = dd >= config.DRAWDOWN_STOP_THRESHOLD * 100
blocked_cons = cons_losses >= config.CONSECUTIVE_LOSS_PAUSE

if paused:
    st.warning(f"⏸ Bot paused (consecutive losses = {cons_losses})")
elif blocked_daily:
    st.error(f"🚫 BLOCKED — daily loss limit hit ({daily_loss:.1f}% / {limit_pct:.0f}%)")
elif blocked_drawdown:
    st.error(f"🚫 BLOCKED — max drawdown hit ({dd:.1f}%)")
elif blocked_cons:
    st.warning(f"⚠️ Consecutive loss pause active ({cons_losses} losses)")
else:
    st.success("✅ Bot is running")

# ---------------------------------------------------------------------------
# Top metrics
# ---------------------------------------------------------------------------

m1, m2, m3, m4, m5 = st.columns(5)

with m1:
    st.metric(
        "Bankroll",
        f"${current:.2f}",
        delta=f"{fmt_usd(current - starting)} all-time",
    )

with m2:
    daily_label = "WIN ▲" if daily_pnl_usd >= 0 else "LOSS ▼"
    st.metric(
        f"Today [{daily_label}]",
        fmt_usd(daily_pnl_usd),
        delta=fmt_pct((-daily_loss) if daily_pnl_usd < 0 else (abs(daily_pnl_usd) / day_start * 100) if day_start > 0 else 0),
        delta_color="normal",
    )

with m3:
    roi = (current - starting) / starting * 100 if starting > 0 else 0.0
    st.metric("Total ROI", fmt_pct(roi), delta=fmt_usd(total_pnl_usd))

with m4:
    st.metric("Peak Bankroll", f"${peak:.2f}", delta=fmt_pct(-dd) if dd > 0 else "at peak")

with m5:
    total_trades = data.get("total_trades", 0)
    wins = data.get("winning_trades", 0)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    st.metric("Win Rate", f"{win_rate:.1f}%", delta=f"{total_trades} trades")

st.divider()

# ---------------------------------------------------------------------------
# Risk panel
# ---------------------------------------------------------------------------

st.subheader("Risk Status")

r1, r2, r3 = st.columns(3)

with r1:
    st.caption(f"Daily Loss Limit  ({daily_loss:.1f}% / {limit_pct:.0f}%)")
    bar_val = min(daily_loss / limit_pct, 1.0) if limit_pct > 0 else 0.0
    st.progress(bar_val)
    if daily_loss >= limit_pct * 0.8:
        st.warning(f"⚠️ {daily_loss:.1f}% — approaching limit!")
    else:
        st.caption(f"Safe — {limit_pct - daily_loss:.1f}% headroom")

with r2:
    dd_stop = config.DRAWDOWN_STOP_THRESHOLD * 100
    dd_reduce = config.DRAWDOWN_REDUCE_THRESHOLD * 100
    st.caption(f"Drawdown from Peak  ({dd:.1f}% / stop at {dd_stop:.0f}%)")
    bar_val2 = min(dd / dd_stop, 1.0) if dd_stop > 0 else 0.0
    st.progress(bar_val2)
    if dd >= dd_reduce:
        st.warning(f"⚠️ Bet size reduced (>{dd_reduce:.0f}% drawdown)")
    else:
        st.caption(f"Normal — reduce at {dd_reduce:.0f}%")

with r3:
    st.caption("Streak")
    streak_label = f"🔥 {cons_wins}W" if cons_wins > 0 else f"❄️ {cons_losses}L"
    st.metric("Current Streak", streak_label)
    pause_at = config.CONSECUTIVE_LOSS_PAUSE
    if cons_losses > 0:
        st.caption(f"{cons_losses}/{pause_at} losses before pause")
    else:
        st.caption("No active losing streak")

st.divider()

# ---------------------------------------------------------------------------
# Open Positions
# ---------------------------------------------------------------------------

st.subheader(f"Open Positions  ({len(open_pos)})")

if open_pos:
    rows = []
    for pos in open_pos.values():
        opened = pos.get("opened_at", "")
        try:
            opened_dt = datetime.fromisoformat(opened).strftime("%Y-%m-%d %H:%M")
        except Exception:
            opened_dt = opened
        unrealized = pos.get("size", 0) - pos.get("cost_basis", 0)
        rows.append({
            "Question": pos.get("question", "")[:60],
            "Side": pos.get("side", ""),
            "Entry $": f"{pos.get('entry_price', 0):.3f}",
            "Shares": f"{pos.get('size', 0):.2f}",
            "Cost": f"${pos.get('cost_basis', 0):.2f}",
            "Unrealized": fmt_usd(unrealized),
            "Opened": opened_dt,
            "Sim": "✓" if pos.get("simulated") else "",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No open positions")

st.divider()

# ---------------------------------------------------------------------------
# Trade History
# ---------------------------------------------------------------------------

st.subheader(f"Trade History  ({len(history)} closed)")

if history:
    rows = []
    for pos in reversed(history):  # most recent first
        pnl = pos.get("pnl") or 0.0
        result = "✅ WIN" if pnl > 0 else "❌ LOSS"
        closed = pos.get("closed_at", "")
        try:
            closed_dt = datetime.fromisoformat(closed).strftime("%Y-%m-%d %H:%M")
        except Exception:
            closed_dt = closed
        rows.append({
            "Result": result,
            "Question": pos.get("question", "")[:60],
            "Side": pos.get("side", ""),
            "Entry $": f"{pos.get('entry_price', 0):.3f}",
            "Exit $": f"{pos.get('exit_price', 0):.3f}" if pos.get("exit_price") else "—",
            "Cost": f"${pos.get('cost_basis', 0):.2f}",
            "P&L": fmt_usd(pnl),
            "Closed": closed_dt,
        })
    df_hist = pd.DataFrame(rows)
    st.dataframe(
        df_hist,
        use_container_width=True,
        hide_index=True,
        column_config={
            "P&L": st.column_config.TextColumn("P&L"),
            "Result": st.column_config.TextColumn("Result"),
        },
    )
else:
    st.info("No closed trades yet")

st.divider()

# ---------------------------------------------------------------------------
# Config snapshot (collapsed)
# ---------------------------------------------------------------------------

with st.expander("⚙️ Bot Configuration"):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Trading**")
        st.write(f"Min Edge: {config.MIN_EDGE_PCT}%")
        st.write(f"Min AI Confidence: {config.MIN_AI_CONFIDENCE:.0%}")
        st.write(f"Bet Size: {config.BET_SIZE_PCT}%")
        st.write(f"Max Bet: ${config.MAX_BET_SIZE}")
        st.write(f"Max Exposure: {config.MAX_EXPOSURE_PCT}%")
    with c2:
        st.markdown("**Risk**")
        st.write(f"Daily Loss Limit: {config.DAILY_LOSS_LIMIT_PCT:.0%}")
        st.write(f"Drawdown Reduce: {config.DRAWDOWN_REDUCE_THRESHOLD:.0%}")
        st.write(f"Drawdown Stop: {config.DRAWDOWN_STOP_THRESHOLD:.0%}")
        st.write(f"Consec. Loss Pause: {config.CONSECUTIVE_LOSS_PAUSE}")
        st.write(f"Pause Duration: {config.PAUSE_DURATION_MINUTES}min")
    with c3:
        st.markdown("**Operational**")
        st.write(f"Poll Interval: {config.POLL_INTERVAL}s")
        st.write(f"AI Model: {config.AI_MODEL}")
        st.write(f"Paper Trading: {config.PAPER_TRADING}")
        st.write(f"Min Volume 24h: ${config.MIN_VOLUME_24H}")
        st.write(f"Min Liquidity: ${config.MIN_LIQUIDITY}")

# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------

st.caption(f"Auto-refreshing every {REFRESH_SECONDS}s")
time.sleep(REFRESH_SECONDS)
st.rerun()
