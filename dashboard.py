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


def build_market_url(pos: dict) -> str:
    market_url = pos.get("market_url", "")
    if market_url:
        return market_url

    event_slug = pos.get("event_slug", "")
    market_slug = pos.get("market_slug", "")
    legacy_slug = pos.get("slug", "")
    condition_id = pos.get("condition_id", "")

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

st.set_page_config(
    page_title="MeQ0L15",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# CSS — Dark crypto terminal theme
# ---------------------------------------------------------------------------

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

  /* ── Base ── */
  html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background-color: #080b12 !important;
    color: #cdd6f4 !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
  }
  [data-testid="stHeader"] { background-color: #080b12 !important; border-bottom: 1px solid #1e2740; }
  [data-testid="stSidebar"] { background-color: #0b0f1a !important; }
  #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }

  /* ── Typography ── */
  p, li { color: #cdd6f4 !important; font-size: 0.875rem; line-height: 1.6; }
  h1, h2, h3, h4 { color: #ffffff !important; font-weight: 700; }
  label { color: #a6adc8 !important; }
  [data-testid="stCaptionContainer"] { color: #6c7086 !important; font-size: 0.75rem !important; }

  /* ── Divider ── */
  hr { border-color: #1e2740 !important; margin: 1.2rem 0; }
  .sep { border-top: 1px solid #1e2740; margin: 20px 0; }

  /* ── Progress bar ── */
  [data-testid="stProgress"] > div {
    background-color: #1e2740; border-radius: 999px; height: 6px;
  }
  [data-testid="stProgress"] > div > div {
    background: linear-gradient(90deg, #4f8ef7, #7c5bf7);
    border-radius: 999px; height: 6px;
  }

  /* ── Expander ── */
  [data-testid="stExpander"] {
    background-color: #0e1420 !important;
    border: 1px solid #1e2740 !important;
    border-radius: 10px !important;
  }
  [data-testid="stExpanderToggleIcon"] { color: #6c7086 !important; }

  /* ── Dataframe ── */
  [data-testid="stDataFrame"] { border: 1px solid #1e2740 !important; border-radius: 10px !important; overflow: hidden; }
  iframe { background: #0e1420 !important; }

  /* ── Alerts ── */
  [data-testid="stAlert"] { border-radius: 8px !important; font-size: 0.85rem !important; }

  /* ══ Custom components ══ */

  /* Header bar */
  .topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 0 10px 0;
    border-bottom: 1px solid #1e2740;
    margin-bottom: 18px;
  }
  .logo {
    display: flex; align-items: center; gap: 10px;
  }
  .logo-icon {
    width: 36px; height: 36px; border-radius: 8px;
    background: linear-gradient(135deg, #ff4466 0%, #7c5bf7 100%);
    display: flex; align-items: center; justify-content: center;
    font-size: 1.2rem; line-height: 1;
    box-shadow: 0 0 16px #ff446640;
  }
  .logo-text {
    font-size: 1.15rem; font-weight: 900; color: #ffffff;
    letter-spacing: 0.12em; line-height: 1;
    text-shadow: 0 0 12px #ff446660;
  }
  .logo-sub { font-size: 0.6rem; color: #6c7086; letter-spacing: 0.18em; margin-top: 3px; text-transform: uppercase; }
  .topbar-meta { display: flex; gap: 28px; align-items: center; }
  .meta-item { text-align: right; }
  .meta-label { font-size: 0.6rem; color: #6c7086; text-transform: uppercase; letter-spacing: 0.1em; }
  .meta-value { font-size: 0.85rem; font-weight: 600; color: #cdd6f4; margin-top: 1px; }
  .meta-value.blue { color: #4f8ef7; }

  /* Status pill */
  .status-pill {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 8px 18px; border-radius: 8px;
    font-size: 0.75rem; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase;
    width: 100%; justify-content: center; margin-bottom: 18px;
  }
  .status-pill .dot {
    width: 7px; height: 7px; border-radius: 50%;
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; } 50% { opacity: 0.3; }
  }
  .pill-green { background: #00ff8714; color: #00ff87; border: 1px solid #00ff8730; }
  .pill-green .dot { background: #00ff87; box-shadow: 0 0 6px #00ff87; }
  .pill-red { background: #ff446614; color: #ff4466; border: 1px solid #ff446630; }
  .pill-red .dot { background: #ff4466; }
  .pill-yellow { background: #fbbf2414; color: #fbbf24; border: 1px solid #fbbf2430; }
  .pill-yellow .dot { background: #fbbf24; }

  /* Badge (mode) */
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 0.6rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  }
  .badge-paper { background: #fbbf2414; color: #fbbf24; border: 1px solid #fbbf2430; }
  .badge-live  { background: #00ff8714; color: #00ff87; border: 1px solid #00ff8730; }

  /* Stat card */
  .stat-card {
    background: #0e1420;
    border: 1px solid #1e2740;
    border-radius: 10px;
    padding: 16px 18px;
    height: 100%;
  }
  .stat-card:hover { border-color: #2e3f60; }
  .stat-label {
    font-size: 0.65rem; font-weight: 600;
    color: #6c7086; text-transform: uppercase;
    letter-spacing: 0.12em; margin-bottom: 8px;
  }
  .stat-value { font-size: 1.55rem; font-weight: 800; color: #ffffff; line-height: 1; }
  .stat-sub { font-size: 0.72rem; color: #6c7086; margin-top: 6px; }

  /* Section header */
  .section-hdr {
    font-size: 0.65rem; font-weight: 700; color: #6c7086;
    text-transform: uppercase; letter-spacing: 0.14em;
    margin-bottom: 12px; display: flex; align-items: center; gap: 8px;
  }
  .section-hdr::after {
    content: ''; flex: 1; height: 1px; background: #1e2740;
  }

  /* Risk panel */
  .risk-card {
    background: #0e1420; border: 1px solid #1e2740;
    border-radius: 10px; padding: 14px 16px;
  }
  .risk-title { font-size: 0.7rem; font-weight: 600; color: #a6adc8; margin-bottom: 8px; }
  .risk-nums { display: flex; justify-content: space-between; font-size: 0.7rem; color: #6c7086; margin-bottom: 6px; }
  .risk-nums .val { color: #cdd6f4; font-weight: 600; }
  .risk-status { font-size: 0.7rem; margin-top: 6px; }

  /* Colors */
  .c-green { color: #00ff87 !important; }
  .c-red   { color: #ff4466 !important; }
  .c-blue  { color: #4f8ef7 !important; }
  .c-yellow{ color: #fbbf24 !important; }
  .c-muted { color: #6c7086 !important; }
  .c-white { color: #ffffff !important; }

  /* Footer */
  .footer {
    text-align: center; color: #2e3f60; font-size: 0.65rem;
    margin-top: 30px; padding-top: 16px; border-top: 1px solid #1e2740;
    letter-spacing: 0.06em;
  }
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

mode_badge = '<span class="badge badge-paper">PAPER</span>' if config.PAPER_TRADING else '<span class="badge badge-live">LIVE</span>'
st.markdown(f"""
<div class="topbar">
  <div class="logo">
    <div class="logo-icon">⚔️</div>
    <div>
      <div class="logo-text">MeQ0L15 &nbsp;{mode_badge}</div>
      <div class="logo-sub">Polymarket War Machine</div>
    </div>
  </div>
  <div class="topbar-meta">
    <div class="meta-item">
      <div class="meta-label">Network</div>
      <div class="meta-value blue">Polygon</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Poll Interval</div>
      <div class="meta-value">{config.POLL_INTERVAL}s</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Last Updated</div>
      <div class="meta-value">{datetime.now().strftime("%H:%M:%S")}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

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
    st.markdown(f'<div class="status-pill pill-yellow"><span class="dot"></span>PAUSED — consecutive losses: {cons_losses}</div>', unsafe_allow_html=True)
elif blocked_daily:
    st.markdown(f'<div class="status-pill pill-red"><span class="dot"></span>BLOCKED — daily loss limit {daily_loss:.1f}% / {limit_pct:.0f}%</div>', unsafe_allow_html=True)
elif blocked_drawdown:
    st.markdown(f'<div class="status-pill pill-red"><span class="dot"></span>BLOCKED — max drawdown {dd:.1f}%</div>', unsafe_allow_html=True)
elif blocked_cons:
    st.markdown(f'<div class="status-pill pill-yellow"><span class="dot"></span>PAUSING — {cons_losses} consecutive losses</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="status-pill pill-green"><span class="dot"></span>BOT ACTIVE — SCANNING MARKETS</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Top metrics
# ---------------------------------------------------------------------------

c1, c2, c3, c4, c5 = st.columns(5)

def pnl_color(v): return "c-green" if v >= 0 else "c-red"

with c1:
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">Bankroll</div>
      <div class="stat-value">${current:.2f}</div>
      <div class="stat-sub c-muted">${open_cost:.2f} in {len(open_pos)} open positions</div>
    </div>''', unsafe_allow_html=True)

with c2:
    color = pnl_color(daily_pnl)
    label = "Today's P&L  ▲" if daily_pnl >= 0 else "Today's P&L  ▼"
    pct = abs(daily_pnl) / day_start * 100 if day_start > 0 else 0
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">{label}</div>
      <div class="stat-value {color}">{fmt_usd(daily_pnl)}</div>
      <div class="stat-sub {color}">{pct:+.1f}% today</div>
    </div>''', unsafe_allow_html=True)

with c3:
    color = pnl_color(roi)
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">Total ROI</div>
      <div class="stat-value {color}">{roi:+.1f}%</div>
      <div class="stat-sub {color}">{fmt_usd(total_pnl)} all-time</div>
    </div>''', unsafe_allow_html=True)

with c4:
    dd_color = "c-red" if dd > 5 else "c-green"
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">Peak Bankroll</div>
      <div class="stat-value">${peak:.2f}</div>
      <div class="stat-sub {dd_color}">{f"−{dd:.1f}% drawdown" if dd > 0 else "✓ At peak"}</div>
    </div>''', unsafe_allow_html=True)

with c5:
    streak = f"🔥 {cons_wins}W streak" if cons_wins > 0 else (f"❄ {cons_losses}L streak" if cons_losses > 0 else "No streak")
    st.markdown(f'''
    <div class="stat-card">
      <div class="stat-label">Win Rate</div>
      <div class="stat-value">{win_rate:.1f}%</div>
      <div class="stat-sub c-muted">{total_trades} trades · {streak}</div>
    </div>''', unsafe_allow_html=True)

st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Risk panel
# ---------------------------------------------------------------------------

st.markdown('<div class="section-hdr">Risk Monitor</div>', unsafe_allow_html=True)

r1, r2, r3 = st.columns(3)

with r1:
    bar = min(daily_loss / limit_pct, 1.0) if limit_pct > 0 else 0.0
    warn = daily_loss >= limit_pct * 0.8
    status_color = "#ff4466" if warn else "#00ff87"
    status_txt = f"⚠ Approaching limit" if warn else f"✓ {limit_pct - daily_loss:.1f}% headroom"
    st.markdown(f'''
    <div class="risk-card">
      <div class="risk-title">Daily Loss Limit</div>
      <div class="risk-nums"><span>Realized loss</span><span class="val">{daily_loss:.1f}% / {limit_pct:.0f}%</span></div>
    </div>''', unsafe_allow_html=True)
    st.progress(bar)
    st.markdown(f'<div class="risk-status" style="color:{status_color}">{status_txt}</div>', unsafe_allow_html=True)

with r2:
    dd_stop = config.DRAWDOWN_STOP_THRESHOLD * 100
    dd_reduce = config.DRAWDOWN_REDUCE_THRESHOLD * 100
    bar2 = min(dd / dd_stop, 1.0) if dd_stop > 0 else 0.0
    warn2 = dd >= dd_reduce
    status_color2 = "#ff4466" if warn2 else "#00ff87"
    status_txt2 = "⚠ Bet size halved" if warn2 else f"✓ Normal sizing"
    st.markdown(f'''
    <div class="risk-card">
      <div class="risk-title">Drawdown from Peak</div>
      <div class="risk-nums"><span>Current drawdown</span><span class="val">{dd:.1f}% / {dd_stop:.0f}%</span></div>
    </div>''', unsafe_allow_html=True)
    st.progress(bar2)
    st.markdown(f'<div class="risk-status" style="color:{status_color2}">{status_txt2}</div>', unsafe_allow_html=True)

with r3:
    pause_at = config.CONSECUTIVE_LOSS_PAUSE
    bar3 = min(cons_losses / pause_at, 1.0) if pause_at > 0 else 0.0
    warn3 = cons_losses >= pause_at * 0.7
    status_color3 = "#ff4466" if warn3 else "#00ff87"
    streak_txt = f"🔥 {cons_wins}W streak" if cons_wins > 0 else (f"❄ {cons_losses}L streak" if cons_losses > 0 else "No streak")
    st.markdown(f'''
    <div class="risk-card">
      <div class="risk-title">Consecutive Losses</div>
      <div class="risk-nums"><span>Loss streak</span><span class="val">{cons_losses} / {pause_at}</span></div>
    </div>''', unsafe_allow_html=True)
    st.progress(bar3)
    st.markdown(f'<div class="risk-status" style="color:{status_color3}">{streak_txt}</div>', unsafe_allow_html=True)

st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Open Positions
# ---------------------------------------------------------------------------

st.markdown(f'<div class="section-hdr">Open Positions ({len(open_pos)})</div>', unsafe_allow_html=True)

if open_pos:
    rows = []
    for pos in open_pos.values():
        opened = pos.get("opened_at", "")
        try:
            opened_dt = datetime.fromisoformat(opened).strftime("%m-%d %H:%M")
        except Exception:
            opened_dt = opened
        max_pnl = pos.get("size", 0) - pos.get("cost_basis", 0)
        url = build_market_url(pos)
        rows.append({
            "Market": pos.get("question", "")[:55],
            "Side": pos.get("side", ""),
            "Entry": f"{pos.get('entry_price', 0):.3f}",
            "Shares": f"{pos.get('size', 0):.2f}",
            "Cost": f"${pos.get('cost_basis', 0):.2f}",
            "Max P&L": f"+${max_pnl:.2f}" if max_pnl >= 0 else f"-${abs(max_pnl):.2f}",
            "Opened": opened_dt,
            "Link": url,
        })
    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="↗ View"),
        },
    )
else:
    st.markdown('<div style="color:#6c7086;font-size:0.8rem;padding:12px 0">No open positions</div>', unsafe_allow_html=True)

st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Trade History
# ---------------------------------------------------------------------------

st.markdown(f'<div class="section-hdr">Trade History ({len(history)} closed)</div>', unsafe_allow_html=True)

if history:
    rows = []
    for pos in reversed(history):
        pnl = pos.get("pnl") or 0.0
        exit_price = pos.get("exit_price")
        if exit_price is not None and 0.01 < float(exit_price) < 0.99:
            result = f"SETTLED @{float(exit_price):.2f}"
        else:
            result = "WIN ▲" if pnl > 0 else "LOSS ▼"
        closed = pos.get("closed_at", "")
        try:
            closed_dt = datetime.fromisoformat(closed).strftime("%m-%d %H:%M")
        except Exception:
            closed_dt = closed
        url = build_market_url(pos)
        rows.append({
            "Result": result,
            "Market": pos.get("question", "")[:55],
            "Side": pos.get("side", ""),
            "Entry": f"{pos.get('entry_price', 0):.3f}",
            "Exit": f"{float(exit_price):.3f}" if exit_price is not None else "—",
            "Cost": f"${pos.get('cost_basis', 0):.2f}",
            "P&L": f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}",
            "Closed": closed_dt,
            "Link": url,
        })
    df_hist = pd.DataFrame(rows)
    st.dataframe(
        df_hist,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="↗ View"),
        },
    )
else:
    st.markdown('<div style="color:#6c7086;font-size:0.8rem;padding:12px 0">No closed trades yet</div>', unsafe_allow_html=True)

st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# AI Usage Stats
# ---------------------------------------------------------------------------

ai = load_ai_stats()
if ai:
    st.markdown('<div class="section-hdr">AI Usage</div>', unsafe_allow_html=True)

    a1, a2, a3, a4 = st.columns(4)
    with a1:
        st.markdown(f'''
        <div class="stat-card">
          <div class="stat-label">Total Calls</div>
          <div class="stat-value">{ai.get("total_calls", 0):,}</div>
          <div class="stat-sub c-muted">{ai.get("model", "—").split("-")[1] if "-" in ai.get("model","") else ai.get("model","—")}</div>
        </div>''', unsafe_allow_html=True)
    with a2:
        st.markdown(f'''
        <div class="stat-card">
          <div class="stat-label">Input Tokens</div>
          <div class="stat-value c-blue">{ai.get("total_input_tokens", 0):,}</div>
          <div class="stat-sub c-muted">$3.00 / MTok</div>
        </div>''', unsafe_allow_html=True)
    with a3:
        st.markdown(f'''
        <div class="stat-card">
          <div class="stat-label">Output Tokens</div>
          <div class="stat-value c-blue">{ai.get("total_output_tokens", 0):,}</div>
          <div class="stat-sub c-muted">$15.00 / MTok</div>
        </div>''', unsafe_allow_html=True)
    with a4:
        cost = ai.get("estimated_cost_usd", 0.0)
        updated = ai.get("updated_at", "")
        try:
            updated = datetime.fromisoformat(updated).strftime("%H:%M:%S")
        except Exception:
            updated = "—"
        cost_color = "c-red" if cost > 1 else "c-green"
        st.markdown(f'''
        <div class="stat-card">
          <div class="stat-label">Est. Cost</div>
          <div class="stat-value {cost_color}">${cost:.4f}</div>
          <div class="stat-sub c-muted">as of {updated}</div>
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

st.markdown(f'<div class="footer">MeQ0L15 · Polymarket War Machine &nbsp;·&nbsp; auto-refresh {REFRESH_SECONDS}s &nbsp;·&nbsp; {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} UTC</div>', unsafe_allow_html=True)

time.sleep(REFRESH_SECONDS)
st.rerun()
