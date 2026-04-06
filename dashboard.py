"""
Polymarket Bot — Streamlit Dashboard (Crypto Dark Theme)
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
AI_STATS_FILE  = Path("data/ai_stats.json")
REFRESH_SECONDS = 10

st.set_page_config(
    page_title="POLYMARKET BOT",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# CSS — Dark crypto terminal theme
# ---------------------------------------------------------------------------

st.markdown("""
<style>
  /* Base */
  html, body, [data-testid="stAppViewContainer"] {
    background-color: #0a0a0f;
    color: #e8eaf0;
    font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
  }
  [data-testid="stMain"] { background-color: #0a0a0f; }
  [data-testid="stHeader"] { background-color: #0a0a0f; }
  [data-testid="stSidebar"] { background-color: #0d0d14; }
  p, span, div, label { color: #e8eaf0; }

  /* Dividers */
  hr { border-color: #1a1a2e !important; }

  /* Metric cards */
  [data-testid="stMetric"] {
    background: linear-gradient(135deg, #0d0d1a 0%, #111120 100%);
    border: 1px solid #1e1e3a;
    border-radius: 8px;
    padding: 16px 20px;
  }
  [data-testid="stMetricLabel"] {
    color: #9ca3af !important;
    font-size: 0.7rem !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }
  [data-testid="stMetricValue"] {
    color: #ffffff !important;
    font-size: 1.6rem !important;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  [data-testid="stMetricDelta"] svg { display: none; }
  [data-testid="stMetricDelta"] > div {
    font-size: 0.75rem !important;
    letter-spacing: 0.05em;
  }

  /* Positive delta = green, negative = red */
  [data-testid="stMetricDeltaPositive"] { color: #00ff87 !important; }
  [data-testid="stMetricDeltaNegative"] { color: #ff4466 !important; }

  /* Dataframes */
  [data-testid="stDataFrame"] {
    border: 1px solid #1e1e3a !important;
    border-radius: 8px;
  }
  .stDataFrame table {
    background-color: #0d0d1a !important;
    color: #e8eaf0 !important;
    font-size: 0.8rem !important;
  }
  .stDataFrame th {
    background-color: #111120 !important;
    color: #c0c8d8 !important;
    text-transform: uppercase;
    font-size: 0.7rem !important;
    letter-spacing: 0.08em;
    border-bottom: 1px solid #2a2a4a !important;
  }
  .stDataFrame td { border-color: #1a1a2e !important; color: #e8eaf0 !important; }
  .stDataFrame tr:hover td { background-color: #161625 !important; }

  /* Progress bar */
  [data-testid="stProgress"] > div {
    background-color: #1a1a2e;
    border-radius: 4px;
  }
  [data-testid="stProgress"] > div > div {
    background: linear-gradient(90deg, #00c6ff, #0072ff);
    border-radius: 4px;
  }

  /* Alerts */
  [data-testid="stAlert"] { border-radius: 6px; font-size: 0.85rem; }

  /* Expander */
  [data-testid="stExpander"] {
    background-color: #0d0d1a;
    border: 1px solid #1e1e3a;
    border-radius: 8px;
  }

  /* Caption */
  [data-testid="stCaptionContainer"] { color: #9ca3af !important; font-size: 0.72rem !important; }

  /* Subheader */
  h2, h3 {
    color: #d1d5db !important;
    font-size: 0.75rem !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-weight: 600;
    margin-bottom: 0.5rem;
  }

  /* Hide streamlit branding */
  #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }

  /* Custom stat card via markdown */
  .stat-card {
    background: linear-gradient(135deg, #0d0d1a 0%, #111120 100%);
    border: 1px solid #1e1e3a;
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 8px;
  }
  .stat-label {
    color: #9ca3af;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 4px;
  }
  .stat-value { font-size: 1.4rem; font-weight: 700; color: #ffffff; }
  .stat-sub { font-size: 0.72rem; color: #9ca3af; margin-top: 2px; }
  .green { color: #00ff87; }
  .red { color: #ff4466; }
  .yellow { color: #fbbf24; }
  .blue { color: #60a5fa; }
  .muted { color: #9ca3af; }

  /* Status badge */
  .badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .badge-green { background: #00ff8722; color: #00ff87; border: 1px solid #00ff8744; }
  .badge-red { background: #ff446622; color: #ff4466; border: 1px solid #ff446644; }
  .badge-yellow { background: #fbbf2422; color: #fbbf24; border: 1px solid #fbbf2444; }
  .badge-blue { background: #60a5fa22; color: #60a5fa; border: 1px solid #60a5fa44; }

  /* Header glow */
  .header-glow {
    font-size: 1.4rem;
    font-weight: 800;
    letter-spacing: 0.15em;
    color: #fff;
    text-shadow: 0 0 20px #6366f1, 0 0 40px #6366f188;
  }

  /* Separator line */
  .sep { border-top: 1px solid #1e1e3a; margin: 16px 0; }

  /* Risk bar wrapper */
  .risk-label {
    display: flex;
    justify-content: space-between;
    font-size: 0.7rem;
    color: #9ca3af;
    margin-bottom: 4px;
  }
  .risk-val { color: #e8eaf0; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_ai_stats() -> dict:
    if not AI_STATS_FILE.exists():
        return {}
    try:
        with open(AI_STATS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def load_portfolio() -> dict | None:
    if not PORTFOLIO_FILE.exists():
        return None
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def fmt_pct(v: float, d: int = 1) -> str:
    return f"{v:+.{d}f}%"

def fmt_usd(v: float) -> str:
    return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"

def is_paused(data: dict) -> bool:
    p = data.get("pause_until")
    if not p:
        return False
    try:
        return datetime.now(timezone.utc) < datetime.fromisoformat(p)
    except Exception:
        return False

def daily_loss_pct(data: dict) -> float:
    day_start = data.get("day_start_bankroll", 0)
    current = data.get("current_bankroll", 0)
    open_cost = sum(p.get("cost_basis", 0) for p in data.get("open_positions", {}).values())
    realized = current + open_cost
    if day_start <= 0:
        return 0.0
    return max(0.0, (day_start - realized) / day_start * 100)

def drawdown_pct(data: dict) -> float:
    peak = data.get("peak_bankroll", 0)
    current = data.get("current_bankroll", 0)
    open_cost = sum(p.get("cost_basis", 0) for p in data.get("open_positions", {}).values())
    realized = current + open_cost
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - realized) / peak * 100)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

data = load_portfolio()

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

h1, h2, h3, h4 = st.columns([5, 1, 1, 1])
with h1:
    mode_badge = '<span class="badge badge-yellow">◈ PAPER</span>' if config.PAPER_TRADING else '<span class="badge badge-green">◉ LIVE</span>'
    st.markdown(
        f'<div class="header-glow">⚡ POLYMARKET BOT</div>'
        f'<div style="margin-top:4px">{mode_badge}</div>',
        unsafe_allow_html=True,
    )
with h2:
    st.markdown(f'<div class="stat-label">Network</div><div class="stat-value blue" style="font-size:1rem">Polygon</div>', unsafe_allow_html=True)
with h3:
    st.markdown(f'<div class="stat-label">Interval</div><div class="stat-value" style="font-size:1rem">{config.POLL_INTERVAL}s</div>', unsafe_allow_html=True)
with h4:
    st.markdown(f'<div class="stat-label">Updated</div><div class="stat-value" style="font-size:1rem">{datetime.now().strftime("%H:%M:%S")}</div>', unsafe_allow_html=True)

st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

if not data:
    st.error("Portfolio file not found — is the bot running?")
    time.sleep(REFRESH_SECONDS)
    st.rerun()

# ---------------------------------------------------------------------------
# Compute values
# ---------------------------------------------------------------------------

current      = data.get("current_bankroll", 0)
day_start    = data.get("day_start_bankroll", 0)
peak         = data.get("peak_bankroll", 0)
starting     = data.get("starting_bankroll", 0)
open_pos     = data.get("open_positions", {})
history      = data.get("trade_history", [])
cons_wins    = data.get("consecutive_wins", 0)
cons_losses  = data.get("consecutive_losses", 0)
total_trades = data.get("total_trades", 0)
winning      = data.get("winning_trades", 0)

open_cost        = sum(p.get("cost_basis", 0) for p in open_pos.values())
realized_bankroll = current + open_cost

daily_loss   = daily_loss_pct(data)
dd           = drawdown_pct(data)
limit_pct    = config.DAILY_LOSS_LIMIT_PCT * 100
paused       = is_paused(data)
daily_pnl    = realized_bankroll - day_start
total_pnl    = realized_bankroll - starting
roi          = (realized_bankroll - starting) / starting * 100 if starting > 0 else 0.0
win_rate     = (winning / total_trades * 100) if total_trades > 0 else 0.0

blocked_daily    = daily_loss >= limit_pct
blocked_drawdown = dd >= config.DRAWDOWN_STOP_THRESHOLD * 100
blocked_cons     = cons_losses >= config.CONSECUTIVE_LOSS_PAUSE

# ---------------------------------------------------------------------------
# Status banner
# ---------------------------------------------------------------------------

if paused:
    st.markdown(f'<div class="badge badge-yellow" style="width:100%;text-align:center;padding:8px">⏸ PAUSED — consecutive losses: {cons_losses}</div>', unsafe_allow_html=True)
elif blocked_daily:
    st.markdown(f'<div class="badge badge-red" style="width:100%;text-align:center;padding:8px">🚫 BLOCKED — daily loss limit {daily_loss:.1f}% / {limit_pct:.0f}%</div>', unsafe_allow_html=True)
elif blocked_drawdown:
    st.markdown(f'<div class="badge badge-red" style="width:100%;text-align:center;padding:8px">🚫 BLOCKED — max drawdown {dd:.1f}%</div>', unsafe_allow_html=True)
elif blocked_cons:
    st.markdown(f'<div class="badge badge-yellow" style="width:100%;text-align:center;padding:8px">⚠ PAUSING — {cons_losses} consecutive losses</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="badge badge-green" style="width:100%;text-align:center;padding:8px">◉ BOT ACTIVE — scanning markets</div>', unsafe_allow_html=True)

st.markdown('<div style="margin-bottom:16px"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Top metrics
# ---------------------------------------------------------------------------

c1, c2, c3, c4, c5 = st.columns(5)

def pnl_color(v): return "green" if v >= 0 else "red"

with c1:
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">Bankroll</div>
      <div class="stat-value">${current:.2f}</div>
      <div class="stat-sub muted">${open_cost:.2f} locked in {len(open_pos)} positions</div>
    </div>''', unsafe_allow_html=True)

with c2:
    color = pnl_color(daily_pnl)
    label = "TODAY WIN ▲" if daily_pnl >= 0 else "TODAY LOSS ▼"
    pct = abs(daily_pnl) / day_start * 100 if day_start > 0 else 0
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">{label}</div>
      <div class="stat-value {color}">{fmt_usd(daily_pnl)}</div>
      <div class="stat-sub {color}">{pct:+.1f}%</div>
    </div>''', unsafe_allow_html=True)

with c3:
    color = pnl_color(roi)
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">Total ROI</div>
      <div class="stat-value {color}">{roi:+.1f}%</div>
      <div class="stat-sub {color}">{fmt_usd(total_pnl)}</div>
    </div>''', unsafe_allow_html=True)

with c4:
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">Peak Bankroll</div>
      <div class="stat-value">${peak:.2f}</div>
      <div class="stat-sub {"red" if dd > 0 else "green"}">{f"-{dd:.1f}% from peak" if dd > 0 else "at peak"}</div>
    </div>''', unsafe_allow_html=True)

with c5:
    streak = f"🔥 {cons_wins}W" if cons_wins > 0 else (f"❄ {cons_losses}L" if cons_losses > 0 else "—")
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">Win Rate</div>
      <div class="stat-value">{win_rate:.1f}%</div>
      <div class="stat-sub muted">{total_trades} trades &nbsp;|&nbsp; {streak}</div>
    </div>''', unsafe_allow_html=True)

st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Risk panel
# ---------------------------------------------------------------------------

st.markdown('<div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:0.12em;color:#4b5563;font-weight:600;margin-bottom:12px">⚠ Risk Status</div>', unsafe_allow_html=True)

r1, r2, r3 = st.columns(3)

with r1:
    bar = min(daily_loss / limit_pct, 1.0) if limit_pct > 0 else 0.0
    color = "#ff4466" if daily_loss >= limit_pct * 0.8 else "#00c6ff"
    headroom = limit_pct - daily_loss
    st.markdown(f'<div class="risk-label"><span>Daily Loss</span><span class="risk-val">{daily_loss:.1f}% / {limit_pct:.0f}%</span></div>', unsafe_allow_html=True)
    st.progress(bar)
    status = f'<span style="color:{color};font-size:0.72rem">{"⚠ Approaching limit" if daily_loss >= limit_pct * 0.8 else f"✓ {headroom:.1f}% headroom"}</span>'
    st.markdown(status, unsafe_allow_html=True)

with r2:
    dd_stop = config.DRAWDOWN_STOP_THRESHOLD * 100
    dd_reduce = config.DRAWDOWN_REDUCE_THRESHOLD * 100
    bar2 = min(dd / dd_stop, 1.0) if dd_stop > 0 else 0.0
    color2 = "#ff4466" if dd >= dd_reduce else "#00c6ff"
    st.markdown(f'<div class="risk-label"><span>Drawdown</span><span class="risk-val">{dd:.1f}% / {dd_stop:.0f}%</span></div>', unsafe_allow_html=True)
    st.progress(bar2)
    status2 = f'<span style="color:{color2};font-size:0.72rem">{"⚠ Bet size reduced" if dd >= dd_reduce else f"✓ Reduce at {dd_reduce:.0f}%"}</span>'
    st.markdown(status2, unsafe_allow_html=True)

with r3:
    pause_at = config.CONSECUTIVE_LOSS_PAUSE
    bar3 = min(cons_losses / pause_at, 1.0) if pause_at > 0 else 0.0
    color3 = "#ff4466" if cons_losses >= pause_at * 0.7 else "#00c6ff"
    st.markdown(f'<div class="risk-label"><span>Loss Streak</span><span class="risk-val">{cons_losses} / {pause_at}</span></div>', unsafe_allow_html=True)
    st.progress(bar3)
    streak_txt = f'🔥 {cons_wins} win streak' if cons_wins > 0 else (f'❄ {cons_losses} loss streak' if cons_losses > 0 else '✓ No streak')
    st.markdown(f'<span style="color:{color3};font-size:0.72rem">{streak_txt}</span>', unsafe_allow_html=True)

st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Open Positions
# ---------------------------------------------------------------------------

st.markdown(f'<div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:0.12em;color:#4b5563;font-weight:600;margin-bottom:12px">◈ Open Positions ({len(open_pos)})</div>', unsafe_allow_html=True)

if open_pos:
    rows = []
    for pos in open_pos.values():
        opened = pos.get("opened_at", "")
        try:
            opened_dt = datetime.fromisoformat(opened).strftime("%m-%d %H:%M")
        except Exception:
            opened_dt = opened
        unrealized = pos.get("size", 0) - pos.get("cost_basis", 0)
        rows.append({
            "Market": pos.get("question", "")[:55],
            "Side": pos.get("side", ""),
            "Entry": f"{pos.get('entry_price', 0):.3f}",
            "Shares": f"{pos.get('size', 0):.2f}",
            "Cost": f"${pos.get('cost_basis', 0):.2f}",
            "Unrealized P&L": f"+${unrealized:.2f}" if unrealized >= 0 else f"-${abs(unrealized):.2f}",
            "Opened": opened_dt,
            "Sim": "✓" if pos.get("simulated") else "●",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.markdown('<div style="color:#4b5563;font-size:0.8rem;padding:12px 0">No open positions</div>', unsafe_allow_html=True)

st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Trade History
# ---------------------------------------------------------------------------

st.markdown(f'<div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:0.12em;color:#4b5563;font-weight:600;margin-bottom:12px">◈ Trade History ({len(history)} closed)</div>', unsafe_allow_html=True)

if history:
    rows = []
    for pos in reversed(history):
        pnl = pos.get("pnl") or 0.0
        result = "WIN ▲" if pnl > 0 else "LOSS ▼"
        closed = pos.get("closed_at", "")
        try:
            closed_dt = datetime.fromisoformat(closed).strftime("%m-%d %H:%M")
        except Exception:
            closed_dt = closed
        rows.append({
            "Result": result,
            "Market": pos.get("question", "")[:55],
            "Side": pos.get("side", ""),
            "Entry": f"{pos.get('entry_price', 0):.3f}",
            "Exit": f"{pos.get('exit_price', 0):.3f}" if pos.get("exit_price") else "—",
            "Cost": f"${pos.get('cost_basis', 0):.2f}",
            "P&L": f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}",
            "Closed": closed_dt,
        })
    df_hist = pd.DataFrame(rows)
    st.dataframe(df_hist, use_container_width=True, hide_index=True)
else:
    st.markdown('<div style="color:#4b5563;font-size:0.8rem;padding:12px 0">No closed trades yet</div>', unsafe_allow_html=True)

st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# AI Usage Stats
# ---------------------------------------------------------------------------

ai = load_ai_stats()
if ai:
    st.markdown('<div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:0.12em;color:#4b5563;font-weight:600;margin-bottom:12px">◈ AI Usage</div>', unsafe_allow_html=True)

    a1, a2, a3, a4 = st.columns(4)
    with a1:
        st.markdown(f'''
        <div class="stat-card">
          <div class="stat-label">Total Calls</div>
          <div class="stat-value">{ai.get("total_calls", 0):,}</div>
          <div class="stat-sub muted">{ai.get("model", "—")}</div>
        </div>''', unsafe_allow_html=True)
    with a2:
        st.markdown(f'''
        <div class="stat-card">
          <div class="stat-label">Input Tokens</div>
          <div class="stat-value blue">{ai.get("total_input_tokens", 0):,}</div>
          <div class="stat-sub muted">$3 / MTok</div>
        </div>''', unsafe_allow_html=True)
    with a3:
        st.markdown(f'''
        <div class="stat-card">
          <div class="stat-label">Output Tokens</div>
          <div class="stat-value blue">{ai.get("total_output_tokens", 0):,}</div>
          <div class="stat-sub muted">$15 / MTok</div>
        </div>''', unsafe_allow_html=True)
    with a4:
        cost = ai.get("estimated_cost_usd", 0.0)
        updated = ai.get("updated_at", "")
        try:
            updated = datetime.fromisoformat(updated).strftime("%H:%M:%S")
        except Exception:
            updated = "—"
        st.markdown(f'''
        <div class="stat-card">
          <div class="stat-label">Est. Cost</div>
          <div class="stat-value {"red" if cost > 1 else "green"}">${cost:.4f}</div>
          <div class="stat-sub muted">updated {updated}</div>
        </div>''', unsafe_allow_html=True)

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Config snapshot
# ---------------------------------------------------------------------------

with st.expander("⚙  Configuration"):
    c1, c2, c3 = st.columns(3)
    def cfg_row(label, val):
        return f'<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #1a1a2e"><span style="color:#9ca3af;font-size:0.72rem">{label}</span><span style="color:#e8eaf0;font-size:0.72rem">{val}</span></div>'
    with c1:
        st.markdown("**TRADING**", unsafe_allow_html=False)
        st.markdown(cfg_row("Min Edge", f"{config.MIN_EDGE_PCT}%") + cfg_row("AI Confidence", f"{config.MIN_AI_CONFIDENCE:.0%}") + cfg_row("Bet Size", f"{config.BET_SIZE_PCT}%") + cfg_row("Max Bet", f"${config.MAX_BET_SIZE}") + cfg_row("Max Exposure", f"{config.MAX_EXPOSURE_PCT}%"), unsafe_allow_html=True)
    with c2:
        st.markdown("**RISK**", unsafe_allow_html=False)
        st.markdown(cfg_row("Daily Loss Limit", f"{config.DAILY_LOSS_LIMIT_PCT:.0%}") + cfg_row("Drawdown Reduce", f"{config.DRAWDOWN_REDUCE_THRESHOLD:.0%}") + cfg_row("Drawdown Stop", f"{config.DRAWDOWN_STOP_THRESHOLD:.0%}") + cfg_row("Consec. Pause At", str(config.CONSECUTIVE_LOSS_PAUSE)) + cfg_row("Pause Duration", f"{config.PAUSE_DURATION_MINUTES}min"), unsafe_allow_html=True)
    with c3:
        st.markdown("**OPERATIONAL**", unsafe_allow_html=False)
        st.markdown(cfg_row("Poll Interval", f"{config.POLL_INTERVAL}s") + cfg_row("AI Model", config.AI_MODEL.split("-")[1] if "-" in config.AI_MODEL else config.AI_MODEL) + cfg_row("Paper Trading", str(config.PAPER_TRADING)) + cfg_row("Min Volume 24h", f"${config.MIN_VOLUME_24H}") + cfg_row("Min Liquidity", f"${config.MIN_LIQUIDITY}"), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown(f'<div style="text-align:center;color:#1e1e3a;font-size:0.65rem;margin-top:24px">auto-refresh {REFRESH_SECONDS}s &nbsp;·&nbsp; {datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")}</div>', unsafe_allow_html=True)

time.sleep(REFRESH_SECONDS)
st.rerun()
