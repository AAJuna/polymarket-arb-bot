"""
Polymarket Bot — Streamlit Dashboard (Cyberpunk Theme)
Auto-refreshes every 10 seconds.
Run: streamlit run dashboard.py
"""

import json
import time
from datetime import datetime, timezone
from html import escape as html_esc
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from utils import format_time_remaining, parse_iso, seconds_until

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------

PORTFOLIO_FILE = Path("data/portfolio.json")
AI_STATS_FILE = Path("data/ai_stats.json")
SHADOW_REPORT_FILE = Path("data/shadow_report.json")
STRATEGY_REPORT_FILE = Path("data/strategy_expectancy.json")
REALTIME_FEED_STATUS_FILE = Path("data/realtime_feed_status.json")
BTC_PORTFOLIO_FILE = Path("data/btc/portfolio.json")
BTC_SIGNAL_FILE = Path("data/btc/signal_status.json")
REFRESH_SECONDS = 10

# ---------------------------------------------------------------------------
# Color tokens
# ---------------------------------------------------------------------------

C_BG = "#000000"
C_PRIMARY = "#00ff41"
C_DANGER = "#ff0044"
C_WARNING = "#ffaa00"
C_TEXT = "#cccccc"
C_TEXT_DIM = "#00ff4160"
C_TEXT_MUTED = "#00ff4140"
C_BORDER = "#00ff4120"
C_CARD_BG = "#00ff4108"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="MeQ0L15",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# CSS — Cyberpunk theme
# ---------------------------------------------------------------------------

st.markdown("""
<style>
  /* ── Base ── */
  html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background-color: #000 !important;
    color: #ccc !important;
    font-family: 'Courier New', Consolas, Monaco, monospace !important;
  }
  [data-testid="stHeader"] { background-color: #000 !important; border-bottom: 1px solid #00ff4120; }
  [data-testid="stSidebar"] { background-color: #000 !important; }
  #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }

  /* ── Scanline overlay ── */
  [data-testid="stAppViewContainer"]::after {
    content: '';
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: repeating-linear-gradient(
      0deg, transparent, transparent 2px,
      #00ff4103 2px, #00ff4103 4px
    );
    pointer-events: none;
    z-index: 9999;
  }

  /* ── Typography ── */
  p, li, span, div { font-family: 'Courier New', Consolas, Monaco, monospace !important; }
  h1, h2, h3, h4 { color: #00ff41 !important; font-weight: 700; font-family: 'Courier New', Consolas, Monaco, monospace !important; }
  label { color: #00ff4160 !important; }

  /* ── Tabs ── */
  [data-testid="stTabs"] { border-bottom: 1px solid #00ff4120; }
  [data-testid="stTabs"] button {
    font-family: 'Courier New', Consolas, Monaco, monospace !important;
    font-size: 0.75rem !important;
    font-weight: 700 !important;
    letter-spacing: 1px !important;
    color: #00ff4150 !important;
    background: transparent !important;
    border: none !important;
    border-bottom: 2px solid transparent !important;
    padding: 10px 20px !important;
  }
  [data-testid="stTabs"] button[aria-selected="true"] {
    color: #00ff41 !important;
    border-bottom: 2px solid #00ff41 !important;
    text-shadow: 0 0 8px #00ff4140;
  }
  [data-testid="stTabs"] button:hover {
    color: #00ff4180 !important;
  }

  /* ── Divider ── */
  hr { border-color: #00ff4120 !important; margin: 1.2rem 0; }
  .sep { border-top: 1px solid #00ff4120; margin: 20px 0; }

  /* ── Plotly ── */
  .js-plotly-plot .plotly .modebar { display: none !important; }

  /* ── Progress bar ── */
  [data-testid="stProgress"] > div {
    background-color: #00ff4115; border-radius: 2px; height: 4px;
  }
  [data-testid="stProgress"] > div > div {
    border-radius: 2px; height: 4px;
  }

  /* ── Expander ── */
  [data-testid="stExpander"] {
    background-color: #00ff4108 !important;
    border: 1px solid #00ff4120 !important;
    border-radius: 0 !important;
  }
  [data-testid="stExpanderToggleIcon"] { color: #00ff4160 !important; }

  /* ── Alerts ── */
  [data-testid="stAlert"] { border-radius: 0 !important; font-size: 0.8rem !important; }

  /* ══ Custom components ══ */

  /* Header bar */
  .topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 0 10px 0;
    border-bottom: 1px solid #00ff4125;
    margin-bottom: 12px;
  }
  .logo { display: flex; align-items: center; gap: 12px; }
  .logo-icon {
    width: 32px; height: 32px;
    border: 2px solid #00ff41;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.1rem; line-height: 1;
  }
  .logo-text {
    font-size: 1rem; font-weight: 900; color: #00ff41;
    letter-spacing: 2px; line-height: 1;
    text-shadow: 0 0 12px #00ff4140;
  }
  .logo-sub {
    font-size: 0.55rem; color: #00ff4160;
    letter-spacing: 3px; margin-top: 3px;
    text-transform: uppercase;
  }
  .topbar-meta { display: flex; gap: 24px; align-items: center; }
  .meta-item { text-align: right; }
  .meta-label {
    font-size: 0.5rem; color: #00ff4160;
    text-transform: uppercase; letter-spacing: 1px;
  }
  .meta-value {
    font-size: 0.75rem; font-weight: 600;
    color: #00ff41; margin-top: 1px;
  }

  /* Mode badge */
  .badge {
    display: inline-block; padding: 2px 8px;
    font-size: 0.55rem; font-weight: 700;
    letter-spacing: 1px; text-transform: uppercase;
  }
  .badge-paper { background: #ffaa0010; color: #ffaa00; border: 1px solid #ffaa0050; }
  .badge-live  { background: #00ff4110; color: #00ff41; border: 1px solid #00ff4150; }

  /* Status pill */
  .status-pill {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 8px 18px;
    font-size: 0.7rem; font-weight: 700;
    letter-spacing: 1px; text-transform: uppercase;
    width: 100%; justify-content: center; margin-bottom: 14px;
  }
  .status-pill .dot {
    width: 7px; height: 7px; border-radius: 50%;
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; } 50% { opacity: 0.3; }
  }
  .pill-green { background: #00ff4110; color: #00ff41; border: 1px solid #00ff4125; }
  .pill-green .dot { background: #00ff41; box-shadow: 0 0 6px #00ff41; }
  .pill-red { background: #ff004410; color: #ff0044; border: 1px solid #ff004425; }
  .pill-red .dot { background: #ff0044; box-shadow: 0 0 6px #ff0044; }
  .pill-yellow { background: #ffaa0010; color: #ffaa00; border: 1px solid #ffaa0025; }
  .pill-yellow .dot { background: #ffaa00; box-shadow: 0 0 6px #ffaa00; }

  /* Stat card */
  .stat-card {
    background: #00ff4108;
    border: 1px solid #00ff4120;
    padding: 14px 16px;
    height: 100%;
    transition: border-color 0.2s;
  }
  .stat-card:hover { border-color: #00ff4140; }
  .stat-label {
    font-size: 0.5rem; font-weight: 600;
    color: #00ff4160; text-transform: uppercase;
    letter-spacing: 2px; margin-bottom: 6px;
  }
  .stat-value {
    font-size: 1.4rem; font-weight: 800;
    color: #00ff41; line-height: 1;
    text-shadow: 0 0 10px #00ff4140;
  }
  .stat-sub { font-size: 0.65rem; color: #00ff4160; margin-top: 4px; }

  /* Section header */
  .section-hdr {
    font-size: 0.6rem; font-weight: 700; color: #00ff4160;
    text-transform: uppercase; letter-spacing: 2px;
    margin-bottom: 12px;
  }

  /* Neon color utilities */
  .c-green { color: #00ff41 !important; text-shadow: 0 0 8px #00ff4140; }
  .c-red   { color: #ff0044 !important; text-shadow: 0 0 8px #ff004440; }
  .c-amber { color: #ffaa00 !important; text-shadow: 0 0 8px #ffaa0040; }
  .c-muted { color: #00ff4160 !important; }
  .c-white { color: #ffffff !important; }

  /* Footer */
  .footer {
    text-align: center; color: #00ff4130;
    font-size: 0.55rem; margin-top: 30px;
    padding-top: 12px; border-top: 1px solid #00ff4115;
    letter-spacing: 1px;
  }

  /* ══ Mobile responsive ══ */
  @media (max-width: 768px) {
    /* Topbar: stack vertically */
    .topbar {
      flex-direction: column;
      align-items: flex-start;
      gap: 10px;
      padding: 10px 0;
    }
    .topbar-meta {
      gap: 14px;
      flex-wrap: wrap;
    }
    .meta-item { text-align: left; }

    /* Stat cards: bigger text */
    .stat-value { font-size: 1.1rem; }
    .stat-label { font-size: 0.5rem; }
    .stat-sub { font-size: 0.6rem; }
    .stat-card { padding: 10px 12px; }

    /* Section headers */
    .section-hdr { font-size: 0.55rem; margin-bottom: 8px; }

    /* Tables: smaller font, force scroll */
    table { font-size: 0.55rem !important; }
    td, th { padding: 5px 6px !important; }

    /* Status pills */
    .status-pill { font-size: 0.6rem; padding: 6px 12px; }

    /* Streamlit columns: override to full width */
    [data-testid="stHorizontalBlock"] {
      flex-wrap: wrap !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
      min-width: 48% !important;
      flex: 1 1 48% !important;
    }
  }

  @media (max-width: 480px) {
    /* Very small screens: single column */
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
      min-width: 100% !important;
      flex: 1 1 100% !important;
    }

    .topbar-meta { gap: 10px; }
    .logo-text { font-size: 0.85rem; }
    .stat-value { font-size: 1rem; }
    .badge { font-size: 0.5rem; padding: 1px 6px; }

    table { font-size: 0.5rem !important; }
    td, th { padding: 4px 4px !important; }
  }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
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


def fmt_pct(v: float, d: int = 1) -> str:
    return f"{v:+.{d}f}%"


def fmt_usd(v: float) -> str:
    return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"


def fmt_end_window(value: str) -> str:
    if not value:
        return "-"
    try:
        end_dt = parse_iso(value)
    except Exception:
        return value
    if seconds_until(end_dt) <= 0:
        return "Ended"
    return format_time_remaining(end_dt)


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


def pnl_color(v: float) -> str:
    return "c-green" if v >= 0 else "c-red"


def neon_stat_card(label: str, value: str, sub: str, color_class: str = "c-green") -> str:
    return f'''
    <div class="stat-card">
      <div class="stat-label">{label}</div>
      <div class="stat-value {color_class}">{value}</div>
      <div class="stat-sub">{sub}</div>
    </div>'''


def plotly_theme() -> dict:
    """Return common Plotly layout settings for cyberpunk theme."""
    return dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Courier New, monospace", color="rgba(0,255,65,0.38)", size=10),
        margin=dict(l=40, r=20, t=30, b=30),
        xaxis=dict(
            gridcolor="rgba(0,255,65,0.06)",
            zerolinecolor="rgba(0,255,65,0.13)",
            tickfont=dict(color="rgba(0,255,65,0.25)", size=9),
        ),
        yaxis=dict(
            gridcolor="rgba(0,255,65,0.06)",
            zerolinecolor="rgba(0,255,65,0.13)",
            tickfont=dict(color="rgba(0,255,65,0.25)", size=9),
        ),
    )

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

data = load_portfolio()
shadow_report = load_json(SHADOW_REPORT_FILE)
strategy_report = load_json(STRATEGY_REPORT_FILE)
ai_stats = load_json(AI_STATS_FILE)
feed_status = load_json(REALTIME_FEED_STATUS_FILE)
btc_data = load_json(BTC_PORTFOLIO_FILE)
btc_signal = load_json(BTC_SIGNAL_FILE)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

mode_badge = (
    '<span class="badge badge-paper">PAPER</span>'
    if config.PAPER_TRADING
    else '<span class="badge badge-live">LIVE</span>'
)

bot_status = "OFFLINE"
status_color = C_DANGER
if data:
    bot_status = "ONLINE"
    status_color = C_PRIMARY

st.html(f"""
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
      <div class="meta-label">Status</div>
      <div class="meta-value" style="color:{status_color}">● {bot_status}</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Network</div>
      <div class="meta-value">POLYGON</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Updated</div>
      <div class="meta-value">{datetime.now().strftime("%H:%M:%S")}</div>
    </div>
  </div>
</div>
""")

if not data:
    st.error("Portfolio file not found — is the bot running?")
    time.sleep(REFRESH_SECONDS)
    st.rerun()

# ---------------------------------------------------------------------------
# Compute values
# ---------------------------------------------------------------------------

current = data.get("current_bankroll", 0)
day_start = data.get("day_start_bankroll", 0)
peak = data.get("peak_bankroll", 0)
starting = data.get("starting_bankroll", 0)
open_pos = data.get("open_positions", {})
history = data.get("trade_history", [])
bankroll_hist = data.get("bankroll_history", [])

# Derive counters from trade_history when snapshot values are stale
if history:
    total_trades = len(history) + len(open_pos)
    winning = sum(1 for t in history if (t.get("pnl") or 0) > 0)
    # Walk history to find current streak
    cons_wins = 0
    cons_losses = 0
    for t in history:
        if (t.get("pnl") or 0) > 0:
            cons_wins += 1
            cons_losses = 0
        else:
            cons_losses += 1
            cons_wins = 0
    # Reconstruct peak from bankroll_history
    if bankroll_hist:
        hist_peak = max(e.get("bankroll", 0) for e in bankroll_hist)
        peak = max(peak, hist_peak)
        data["peak_bankroll"] = peak
else:
    cons_wins = data.get("consecutive_wins", 0)
    cons_losses = data.get("consecutive_losses", 0)
    total_trades = data.get("total_trades", 0)
    winning = data.get("winning_trades", 0)

open_cost = sum(p.get("cost_basis", 0) for p in open_pos.values())
realized_bankroll = current + open_cost

daily_loss = daily_loss_pct(data)
dd = drawdown_pct(data)
limit_pct = config.DAILY_LOSS_LIMIT_PCT * 100
paused = is_paused(data)
daily_pnl = realized_bankroll - day_start
total_pnl = realized_bankroll - starting
roi = (realized_bankroll - starting) / starting * 100 if starting > 0 else 0.0
win_rate = (winning / total_trades * 100) if total_trades > 0 else 0.0

blocked_daily = daily_loss >= limit_pct
blocked_drawdown = dd >= config.DRAWDOWN_STOP_THRESHOLD * 100
blocked_cons = cons_losses >= config.CONSECUTIVE_LOSS_PAUSE

# ---------------------------------------------------------------------------
# Historical risk metrics (all-time from trade_history)
# ---------------------------------------------------------------------------
max_loss_streak = 0
max_drawdown_pct = 0.0
total_lost = 0.0
if history:
    # Max loss streak
    streak = 0
    for t in history:
        if (t.get("pnl") or 0) <= 0:
            streak += 1
            max_loss_streak = max(max_loss_streak, streak)
        else:
            streak = 0
    # Total losses
    total_lost = sum(abs(t.get("pnl") or 0) for t in history if (t.get("pnl") or 0) < 0)
    # Max drawdown from bankroll history
    if bankroll_hist:
        running_peak = 0.0
        for entry in bankroll_hist:
            b = entry.get("bankroll", 0)
            running_peak = max(running_peak, b)
            if running_peak > 0:
                dd_hist = (running_peak - b) / running_peak * 100
                max_drawdown_pct = max(max_drawdown_pct, dd_hist)

# ---------------------------------------------------------------------------
# Status banner
# ---------------------------------------------------------------------------

if paused:
    st.html(f'<div class="status-pill pill-yellow"><span class="dot"></span>PAUSED — CONSECUTIVE LOSSES: {cons_losses}</div>')
elif blocked_daily:
    st.html(f'<div class="status-pill pill-red"><span class="dot"></span>BLOCKED — DAILY LOSS LIMIT {daily_loss:.1f}% / {limit_pct:.0f}%</div>')
elif blocked_drawdown:
    st.html(f'<div class="status-pill pill-red"><span class="dot"></span>BLOCKED — MAX DRAWDOWN {dd:.1f}%</div>')
elif blocked_cons:
    st.html(f'<div class="status-pill pill-yellow"><span class="dot"></span>PAUSING — {cons_losses} CONSECUTIVE LOSSES</div>')
else:
    st.html('<div class="status-pill pill-green"><span class="dot"></span>BOT ACTIVE — SCANNING MARKETS</div>')

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_overview, tab_positions, tab_analytics, tab_risk, tab_btc, tab_config = st.tabs([
    "[ OVERVIEW ]", "[ POSITIONS ]", "[ ANALYTICS ]", "[ RISK ]", "[ BTC 5M ]", "[ CONFIG ]"
])

# ---------------------------------------------------------------------------
# TAB: OVERVIEW
# ---------------------------------------------------------------------------

with tab_overview:
    # Hero stat cards
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.html(neon_stat_card(
            "TOTAL EQUITY",
            f"${realized_bankroll:,.2f}",
            f"{fmt_usd(total_pnl)} ({roi:+.1f}% ROI)",
            pnl_color(total_pnl),
        ))

    with c2:
        daily_pct = abs(daily_pnl) / day_start * 100 if day_start > 0 else 0
        st.html(neon_stat_card(
            "TODAY P&L",
            fmt_usd(daily_pnl),
            f"{fmt_pct(daily_pct if daily_pnl >= 0 else -daily_pct)} today",
            pnl_color(daily_pnl),
        ))

    with c3:
        streak = f"W{cons_wins}" if cons_wins > 0 else (f"L{cons_losses}" if cons_losses > 0 else "—")
        st.html(neon_stat_card(
            "WIN RATE",
            f"{win_rate:.1f}%",
            f"{total_trades} trades · {streak}",
            "c-amber",
        ))

    with c4:
        st.html(neon_stat_card(
            "OPEN EXPOSURE",
            f"${open_cost:,.2f}",
            f"{len(open_pos)} positions active",
            "c-white",
        ))

    st.html('<div class="sep"></div>')

    # Equity Curve Chart
    st.html('<div class="section-hdr">// EQUITY CURVE</div>')

    if bankroll_hist:
        eq_df = pd.DataFrame(bankroll_hist)
        eq_df["timestamp"] = pd.to_datetime(eq_df["timestamp"])
        eq_df = eq_df.sort_values("timestamp")

        fig_equity = go.Figure()
        fig_equity.add_trace(go.Scatter(
            x=eq_df["timestamp"],
            y=eq_df["bankroll"],
            mode="lines",
            line=dict(color="#00ff41", width=2),
            fill="tozeroy",
            fillcolor="rgba(0,255,65,0.08)",
            hovertemplate="$%{y:,.2f}<br>%{x|%b %d %H:%M}<extra></extra>",
        ))

        # Trade markers on equity curve
        if history:
            for trade in history[-20:]:
                closed_at = trade.get("closed_at", "")
                pnl_val = trade.get("pnl", 0) or 0
                if not closed_at:
                    continue
                try:
                    trade_time = pd.to_datetime(closed_at)
                except Exception:
                    continue
                idx = eq_df["timestamp"].searchsorted(trade_time)
                if idx >= len(eq_df):
                    idx = len(eq_df) - 1
                bankroll_at = eq_df.iloc[idx]["bankroll"]
                marker_color = "#00ff41" if pnl_val >= 0 else "#ff0044"
                fig_equity.add_trace(go.Scatter(
                    x=[trade_time],
                    y=[bankroll_at],
                    mode="markers",
                    marker=dict(
                        color=marker_color,
                        size=8,
                        symbol="diamond",
                        line=dict(width=1, color=marker_color),
                    ),
                    hovertemplate=(
                        f"{html_esc(trade.get('question', '')[:40])}<br>"
                        f"P&L: {fmt_usd(pnl_val)}<extra></extra>"
                    ),
                    showlegend=False,
                ))

        fig_equity.update_layout(
            **plotly_theme(),
            height=250,
            showlegend=False,
            yaxis_tickprefix="$",
        )
        st.plotly_chart(fig_equity, width="stretch", config={"displayModeBar": False})
    else:
        st.html(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO EQUITY DATA YET — SNAPSHOTS RECORDED EACH SAVE CYCLE</div>',
        )

    st.html('<div class="sep"></div>')

    # Bottom row: Risk mini + Recent trades
    bot_left, bot_right = st.columns(2)

    with bot_left:
        st.html('<div class="section-hdr">// RISK STATUS</div>')

        # --- All-time metrics (primary display) ---
        dd_stop = config.DRAWDOWN_STOP_THRESHOLD * 100
        pause_at = config.CONSECUTIVE_LOSS_PAUSE
        loss_pct = (total_lost / starting * 100) if starting > 0 else 0.0

        mdd_ratio = min(max_drawdown_pct / dd_stop, 1.0) if dd_stop > 0 else 0.0
        mdd_color = C_DANGER if max_drawdown_pct >= dd_stop * 0.8 else (C_WARNING if max_drawdown_pct >= dd_stop * 0.5 else C_PRIMARY)
        st.html(f'''
        <div style="display:flex;justify-content:space-between;font-size:0.6rem;color:{mdd_color};margin-bottom:4px;">
          <span>MAX DRAWDOWN</span><span>{max_drawdown_pct:.1f}% / {dd_stop:.0f}%</span>
        </div>
        <div style="height:4px;background:#ffaa0015;border-radius:2px;overflow:hidden;margin-bottom:12px;">
          <div style="width:{mdd_ratio*100:.0f}%;height:100%;background:{mdd_color};box-shadow:0 0 6px {mdd_color};border-radius:2px;"></div>
        </div>''')

        mls_ratio = min(max_loss_streak / pause_at, 1.0) if pause_at > 0 else 0.0
        mls_color = C_DANGER if max_loss_streak >= pause_at * 0.7 else (C_WARNING if max_loss_streak >= pause_at * 0.4 else C_PRIMARY)
        st.html(f'''
        <div style="display:flex;justify-content:space-between;font-size:0.6rem;color:{mls_color};margin-bottom:4px;">
          <span>MAX LOSS STREAK</span><span>{max_loss_streak} / {pause_at}</span>
        </div>
        <div style="height:4px;background:#00ff4115;border-radius:2px;overflow:hidden;margin-bottom:12px;">
          <div style="width:{mls_ratio*100:.0f}%;height:100%;background:{mls_color};box-shadow:0 0 6px {mls_color};border-radius:2px;"></div>
        </div>''')

        tl_color = C_DANGER if total_lost >= starting * 0.3 else (C_WARNING if total_lost >= starting * 0.15 else C_PRIMARY)
        st.html(f'''
        <div style="display:flex;justify-content:space-between;font-size:0.6rem;color:{tl_color};margin-bottom:4px;">
          <span>TOTAL LOST</span><span>${total_lost:.2f} ({loss_pct:.1f}%)</span>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:0.6rem;color:#555;margin-bottom:4px;">
          <span>WIN RATE</span><span>{win_rate:.0f}% ({winning}W / {total_trades - winning}L)</span>
        </div>''')

        # --- Live circuit-breaker status (compact) ---
        dl_color = C_DANGER if daily_loss >= limit_pct * 0.8 else (C_WARNING if daily_loss >= limit_pct * 0.5 else "#555")
        dd_live_color = C_DANGER if dd >= dd_stop * 0.8 else "#555"
        cl_live_color = C_DANGER if cons_losses >= pause_at * 0.7 else "#555"
        st.html(f'''
        <div style="margin-top:10px;padding-top:8px;border-top:1px solid #00ff4115;">
          <div style="color:{C_PRIMARY};font-size:0.5rem;margin-bottom:6px;letter-spacing:1px;">LIVE CIRCUIT BREAKERS</div>
          <div style="display:flex;gap:12px;font-size:0.55rem;">
            <span style="color:{dl_color};">DL {daily_loss:.1f}%</span>
            <span style="color:{dd_live_color};">DD {dd:.1f}%</span>
            <span style="color:{cl_live_color};">LS {cons_losses}/{pause_at}</span>
          </div>
        </div>''')

    with bot_right:
        st.html('<div class="section-hdr">// RECENT TRADES</div>')

        if history:
            recent = list(reversed(history))[:5]
            rows_html = ""
            for t in recent:
                pnl_val = t.get("pnl", 0) or 0
                pc = "color:#00ff41" if pnl_val >= 0 else "color:#ff0044"
                market = html_esc(t.get("question", "")[:35])
                side = t.get("side", "")
                side_color = "#00ff41" if side == "YES" else "#ff0044"
                rows_html += f'''
                <div style="display:flex;justify-content:space-between;padding:3px 0;font-size:0.65rem;">
                  <span style="color:#ccc;flex:2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{market}</span>
                  <span style="color:{side_color};flex:0.4;text-align:center;">{side}</span>
                  <span style="{pc};flex:0.6;text-align:right;">{fmt_usd(pnl_val)}</span>
                </div>'''
            st.html(f'''
            <div style="border:1px solid #00ff4120;padding:10px;background:#00ff4105;">
              <div style="display:flex;justify-content:space-between;font-size:0.55rem;color:#00ff4140;border-bottom:1px solid #00ff4110;padding-bottom:4px;margin-bottom:4px;">
                <span style="flex:2;">MARKET</span><span style="flex:0.4;text-align:center;">SIDE</span><span style="flex:0.6;text-align:right;">P&L</span>
              </div>
              {rows_html}
            </div>''')
        else:
            st.html(
                '<div style="color:#00ff4140;font-size:0.65rem;padding:12px 0">// NO CLOSED TRADES YET</div>',
            )

# ---------------------------------------------------------------------------
# TAB: POSITIONS (Task 5)
# ---------------------------------------------------------------------------

with tab_positions:
    # --- Open Positions Table ---
    st.html(f'<div class="section-hdr">// OPEN POSITIONS [{len(open_pos)}]</div>')

    if open_pos:
        rows_html = ""
        for pos_id, pos in open_pos.items():
            question = html_esc(pos.get("question", "")[:50])
            side = pos.get("side", "")
            if side == "YES":
                side_badge = (
                    '<span style="background:#00ff4115;color:#00ff41;border:1px solid #00ff4130;'
                    'border-radius:3px;padding:1px 6px;font-size:0.6rem;letter-spacing:0.05em;">YES</span>'
                )
            else:
                side_badge = (
                    '<span style="background:#ff004415;color:#ff0044;border:1px solid #ff004430;'
                    'border-radius:3px;padding:1px 6px;font-size:0.6rem;letter-spacing:0.05em;">NO</span>'
                )
            entry = pos.get("entry_price", 0)
            shares = pos.get("size", 0)
            cost = pos.get("cost_basis", 0)
            max_pnl = shares - cost
            end_date = pos.get("end_date", "")
            ends_str = fmt_end_window(end_date)
            url = build_market_url(pos)
            rows_html += f"""
            <tr style="border-bottom:1px solid #00ff4110;">
              <td style="padding:7px 8px;color:#ccc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:260px;">{question}</td>
              <td style="padding:7px 8px;text-align:center;">{side_badge}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{entry:.3f}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{shares:.2f}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{fmt_usd(cost)}</td>
              <td style="padding:7px 8px;text-align:right;color:#00ff41;text-shadow:0 0 6px #00ff4180;">{fmt_usd(max_pnl)}</td>
              <td style="padding:7px 8px;text-align:right;color:{C_WARNING};">{ends_str}</td>
              <td style="padding:7px 8px;text-align:center;"><a href="{url}" target="_blank" style="color:#00ff4180;text-decoration:none;font-size:0.75rem;">&#8599;</a></td>
            </tr>"""
        st.html(f"""
        <div style="overflow-x:auto;">
          <table style="width:100%;border-collapse:collapse;font-family:monospace;font-size:0.65rem;">
            <thead>
              <tr style="border-bottom:1px solid #00ff4130;color:#00ff4160;font-size:0.55rem;letter-spacing:0.08em;">
                <th style="padding:6px 8px;text-align:left;">MARKET</th>
                <th style="padding:6px 8px;text-align:center;">SIDE</th>
                <th style="padding:6px 8px;text-align:right;">ENTRY</th>
                <th style="padding:6px 8px;text-align:right;">SHARES</th>
                <th style="padding:6px 8px;text-align:right;">COST</th>
                <th style="padding:6px 8px;text-align:right;">MAX P&amp;L</th>
                <th style="padding:6px 8px;text-align:right;">ENDS</th>
                <th style="padding:6px 8px;text-align:center;">&#8599;</th>
              </tr>
            </thead>
            <tbody>{rows_html}
            </tbody>
          </table>
        </div>
        """)
    else:
        st.html(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO OPEN POSITIONS</div>',
        )

    st.html('<div class="sep"></div>')

    # --- Trade History Table ---
    st.html(f'<div class="section-hdr">// TRADE HISTORY [{len(history)} CLOSED]</div>')

    if history:
        rows_html = ""
        for pos in reversed(history):
            exit_price = pos.get("exit_price") or 0.0
            pnl_val = pos.get("pnl", 0) or 0
            # Determine result badge
            if 0.01 <= exit_price <= 0.99:
                result_badge = (
                    f'<span style="background:#ffaa0025;color:{C_WARNING};border:1px solid #ffaa0040;'
                    f'border-radius:3px;padding:1px 6px;font-size:0.6rem;">@{exit_price:.2f}</span>'
                )
            elif pnl_val > 0:
                result_badge = (
                    '<span style="background:#00ff4125;color:#00ff41;border:1px solid #00ff4140;'
                    'border-radius:3px;padding:1px 6px;font-size:0.6rem;">WIN &#9650;</span>'
                )
            else:
                result_badge = (
                    '<span style="background:#ff004425;color:#ff0044;border:1px solid #ff004440;'
                    'border-radius:3px;padding:1px 6px;font-size:0.6rem;">LOSS &#9660;</span>'
                )
            question = html_esc(pos.get("question", "")[:50])
            side = pos.get("side", "")
            side_color = C_PRIMARY if side == "YES" else C_DANGER
            entry = pos.get("entry_price", 0)
            pnl_col = C_PRIMARY if pnl_val >= 0 else C_DANGER
            closed_at_raw = pos.get("closed_at", "")
            try:
                closed_dt = pd.to_datetime(closed_at_raw)
                closed_str = closed_dt.strftime("%b %d %H:%M")
            except Exception:
                closed_str = closed_at_raw[:16] if closed_at_raw else "-"
            url = build_market_url(pos)
            rows_html += f"""
            <tr style="border-bottom:1px solid #00ff4110;">
              <td style="padding:7px 8px;text-align:center;">{result_badge}</td>
              <td style="padding:7px 8px;color:#ccc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px;">{question}</td>
              <td style="padding:7px 8px;text-align:center;color:{side_color};">{side}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{entry:.3f}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{exit_price:.3f}</td>
              <td style="padding:7px 8px;text-align:right;color:{pnl_col};text-shadow:0 0 6px {pnl_col}80;">{fmt_usd(pnl_val)}</td>
              <td style="padding:7px 8px;text-align:right;color:#666;">{closed_str}</td>
              <td style="padding:7px 8px;text-align:center;"><a href="{url}" target="_blank" style="color:#00ff4180;text-decoration:none;font-size:0.75rem;">&#8599;</a></td>
            </tr>"""
        st.html(f"""
        <div style="overflow-x:auto;">
          <table style="width:100%;border-collapse:collapse;font-family:monospace;font-size:0.65rem;">
            <thead>
              <tr style="border-bottom:1px solid #00ff4130;color:#00ff4160;font-size:0.55rem;letter-spacing:0.08em;">
                <th style="padding:6px 8px;text-align:center;">RESULT</th>
                <th style="padding:6px 8px;text-align:left;">MARKET</th>
                <th style="padding:6px 8px;text-align:center;">SIDE</th>
                <th style="padding:6px 8px;text-align:right;">ENTRY</th>
                <th style="padding:6px 8px;text-align:right;">EXIT</th>
                <th style="padding:6px 8px;text-align:right;">P&amp;L</th>
                <th style="padding:6px 8px;text-align:right;">CLOSED</th>
                <th style="padding:6px 8px;text-align:center;">&#8599;</th>
              </tr>
            </thead>
            <tbody>{rows_html}
            </tbody>
          </table>
        </div>
        """)
    else:
        st.html(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO CLOSED TRADES YET</div>',
        )

    st.html('<div class="sep"></div>')

    # --- P&L Distribution Chart ---
    st.html('<div class="section-hdr">// P&L DISTRIBUTION</div>')

    if history:
        pnl_values = [p.get("pnl", 0) or 0 for p in history]
        bar_colors = [C_PRIMARY if v >= 0 else C_DANGER for v in pnl_values]
        hover_texts = [
            f"{html_esc(p.get('question', '')[:40])}<br>{fmt_usd(p.get('pnl', 0) or 0)}"
            for p in history
        ]
        fig_pnl = go.Figure()
        fig_pnl.add_trace(go.Bar(
            x=list(range(len(pnl_values))),
            y=pnl_values,
            marker_color=bar_colors,
            hovertext=hover_texts,
            hoverinfo="text",
            showlegend=False,
        ))
        layout = plotly_theme()
        layout.update({
            "height": 200,
            "showlegend": False,
            "shapes": [{
                "type": "line",
                "xref": "paper", "x0": 0, "x1": 1,
                "yref": "y", "y0": 0, "y1": 0,
                "line": {"color": "rgba(255,255,255,0.15)", "width": 1},
            }],
            "yaxis": {**layout.get("yaxis", {}), "tickprefix": "$"},
            "xaxis": {**layout.get("xaxis", {}), "showticklabels": False},
        })
        fig_pnl.update_layout(**layout)
        st.plotly_chart(fig_pnl, width="stretch", config={"displayModeBar": False})
    else:
        st.html(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO TRADE DATA</div>',
        )

# ---------------------------------------------------------------------------
# TAB: ANALYTICS (Task 6)
# ---------------------------------------------------------------------------

with tab_analytics:

    # ── Strategy Expectancy ──────────────────────────────────────────────────
    st.html('<div class="section-hdr">// STRATEGY EXPECTANCY</div>')

    strat_data = strategy_report.get("by_strategy") or {}

    if strat_data:
        NEON_COLORS = ["#00ff41", "#ffaa00", "#ff0044", "#00e5ff", "#ff00ff", "#ffffff"]
        strat_names = list(strat_data.keys())
        strat_trades = [strat_data[s].get("trades", 0) for s in strat_names]
        total_trade_count = sum(strat_trades)
        slice_colors = [NEON_COLORS[i % len(NEON_COLORS)] for i in range(len(strat_names))]

        col_donut, col_legend = st.columns([1, 1])

        with col_donut:
            fig_donut = go.Figure(go.Pie(
                labels=strat_names,
                values=strat_trades,
                hole=0.6,
                marker=dict(
                    colors=slice_colors,
                    line=dict(color="#000000", width=2),
                ),
                textinfo="none",
                hovertemplate="%{label}<br>%{value} trades (%{percent})<extra></extra>",
            ))
            fig_donut.add_annotation(
                text=f"<b>{total_trade_count}</b><br><span style='font-size:10px'>TRADES</span>",
                x=0.5, y=0.5,
                font=dict(family="Courier New, monospace", color="#00ff41", size=18),
                showarrow=False,
            )
            fig_donut.update_layout(
                **plotly_theme(),
                height=260,
                showlegend=False,
            )
            st.plotly_chart(fig_donut, width="stretch", config={"displayModeBar": False})

        with col_legend:
            legend_html = ""
            for i, sname in enumerate(strat_names):
                s = strat_data[sname]
                color = NEON_COLORS[i % len(NEON_COLORS)]
                wr = s.get("win_rate", 0) or 0
                n_trades = s.get("trades", 0) or 0
                avg_pnl = s.get("avg_pnl", 0) or 0
                pnl_col = "#00ff41" if avg_pnl >= 0 else "#ff0044"
                legend_html += f"""
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
                  <div style="width:12px;height:12px;background:{color};box-shadow:0 0 6px {color};flex-shrink:0;"></div>
                  <div>
                    <div style="font-size:0.7rem;color:#ccc;font-weight:600;">{sname}</div>
                    <div style="font-size:0.6rem;color:#00ff4160;">
                      WR {wr:.1f}% &nbsp;·&nbsp; {n_trades} trades &nbsp;·&nbsp;
                      <span style="color:{pnl_col};">avg {fmt_usd(avg_pnl)}</span>
                    </div>
                  </div>
                </div>"""
            st.html(f'<div style="padding:10px 0;">{legend_html}</div>')

        # Strategy table
        tbl_rows = ""
        for sname, s in strat_data.items():
            wr = s.get("win_rate", 0) or 0
            wr_col = "#00ff41" if wr >= 50 else "#ff0044"
            avg_pnl_v = s.get("avg_pnl", 0) or 0
            total_pnl_v = s.get("total_pnl", 0) or 0
            avg_edge_v = s.get("avg_edge_pct", 0) or 0
            avg_ai_v = s.get("avg_ai_confidence", 0) or 0
            pnl_col = "#00ff41" if avg_pnl_v >= 0 else "#ff0044"
            tot_col = "#00ff41" if total_pnl_v >= 0 else "#ff0044"
            tbl_rows += f"""
            <tr style="border-bottom:1px solid #00ff4110;">
              <td style="padding:7px 8px;color:#ccc;">{sname}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{s.get("trades", 0)}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{s.get("resolved_trades", 0)}</td>
              <td style="padding:7px 8px;text-align:right;color:{wr_col};text-shadow:0 0 6px {wr_col}80;">{wr:.1f}%</td>
              <td style="padding:7px 8px;text-align:right;color:{pnl_col};text-shadow:0 0 6px {pnl_col}80;">{fmt_usd(avg_pnl_v)}</td>
              <td style="padding:7px 8px;text-align:right;color:{tot_col};text-shadow:0 0 6px {tot_col}80;">{fmt_usd(total_pnl_v)}</td>
              <td style="padding:7px 8px;text-align:right;color:#00e5ff;">{avg_edge_v:.1f}%</td>
              <td style="padding:7px 8px;text-align:right;color:#ff00ff;">{avg_ai_v:.1f}%</td>
            </tr>"""
        st.html(f"""
        <div style="overflow-x:auto;margin-top:12px;">
          <table style="width:100%;border-collapse:collapse;font-family:monospace;font-size:0.65rem;">
            <thead>
              <tr style="border-bottom:1px solid #00ff4130;color:#00ff4160;font-size:0.55rem;letter-spacing:0.08em;">
                <th style="padding:6px 8px;text-align:left;">STRATEGY</th>
                <th style="padding:6px 8px;text-align:right;">TRADES</th>
                <th style="padding:6px 8px;text-align:right;">RESOLVED</th>
                <th style="padding:6px 8px;text-align:right;">WIN RATE</th>
                <th style="padding:6px 8px;text-align:right;">AVG P&amp;L</th>
                <th style="padding:6px 8px;text-align:right;">TOTAL P&amp;L</th>
                <th style="padding:6px 8px;text-align:right;">AVG EDGE</th>
                <th style="padding:6px 8px;text-align:right;">AVG AI</th>
              </tr>
            </thead>
            <tbody>{tbl_rows}
            </tbody>
          </table>
        </div>
        """)
    else:
        st.html(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO STRATEGY DATA YET</div>',
        )

    st.html('<div class="sep"></div>')

    # ── Shadow Fill Report ───────────────────────────────────────────────────
    st.html('<div class="section-hdr">// SHADOW FILL REPORT</div>')

    shadow_by_strat = shadow_report.get("by_strategy") or {}
    shadow_recent = shadow_report.get("recent_resolved") or []

    if shadow_by_strat:
        shadow_rows = ""
        for sname, s in shadow_by_strat.items():
            wr_s = s.get("win_rate", 0) or 0
            wr_col = "#00ff41" if wr_s >= 50 else "#ff0044"
            exp = s.get("expected_value_per_dollar", s.get("exp_per_dollar", 0)) or 0
            exp_col = "#00ff41" if exp >= 0 else "#ff0044"
            avg_edge_s = s.get("avg_edge_pct", 0) or 0
            shadow_rows += f"""
            <tr style="border-bottom:1px solid #00ff4110;">
              <td style="padding:7px 8px;color:#ccc;">{sname}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{s.get("total_signals", s.get("signals", 0))}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{s.get("resolved", 0)}</td>
              <td style="padding:7px 8px;text-align:right;color:#aaa;">{s.get("open", 0)}</td>
              <td style="padding:7px 8px;text-align:right;color:{wr_col};text-shadow:0 0 6px {wr_col}80;">{wr_s:.1f}%</td>
              <td style="padding:7px 8px;text-align:right;color:{exp_col};text-shadow:0 0 6px {exp_col}80;">{exp:+.3f}</td>
              <td style="padding:7px 8px;text-align:right;color:#00e5ff;">{avg_edge_s:.1f}%</td>
            </tr>"""
        st.html(f"""
        <div style="overflow-x:auto;">
          <table style="width:100%;border-collapse:collapse;font-family:monospace;font-size:0.65rem;">
            <thead>
              <tr style="border-bottom:1px solid #00ff4130;color:#00ff4160;font-size:0.55rem;letter-spacing:0.08em;">
                <th style="padding:6px 8px;text-align:left;">STRATEGY</th>
                <th style="padding:6px 8px;text-align:right;">SIGNALS</th>
                <th style="padding:6px 8px;text-align:right;">RESOLVED</th>
                <th style="padding:6px 8px;text-align:right;">OPEN</th>
                <th style="padding:6px 8px;text-align:right;">WIN RATE</th>
                <th style="padding:6px 8px;text-align:right;">EXP / $1</th>
                <th style="padding:6px 8px;text-align:right;">AVG EDGE</th>
              </tr>
            </thead>
            <tbody>{shadow_rows}
            </tbody>
          </table>
        </div>
        """)
    else:
        st.html(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO SHADOW DATA</div>',
        )

    if shadow_recent:
        st.html('<div style="font-size:0.55rem;color:#00ff4160;letter-spacing:2px;margin:14px 0 6px 0;">RECENT RESOLVED</div>')
        recent_rows = ""
        for item in shadow_recent:
            pnl_per_dollar = item.get("pnl_per_dollar", 0) or 0
            pnl_col = "#00ff41" if pnl_per_dollar >= 0 else "#ff0044"
            question = html_esc((item.get("question") or "")[:50])
            strat_label = item.get("strategy_type", "-")
            resolved_at = (item.get("resolved_at") or "")[:16]
            url = item.get("market_url", "")
            link_html = (
                f'<a href="{url}" target="_blank" style="color:#00ff4180;text-decoration:none;font-size:0.75rem;">&#8599;</a>'
                if url else ""
            )
            recent_rows += f"""
            <div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.65rem;border-bottom:1px solid #00ff4108;">
              <span style="color:#00ff4160;flex-shrink:0;width:80px;">{strat_label[:12]}</span>
              <span style="color:#ccc;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{question}</span>
              <span style="color:{pnl_col};text-shadow:0 0 6px {pnl_col}80;flex-shrink:0;width:60px;text-align:right;">{pnl_per_dollar:+.3f}</span>
              <span style="color:#666;flex-shrink:0;width:90px;text-align:right;">{resolved_at}</span>
              <span style="flex-shrink:0;width:16px;text-align:center;">{link_html}</span>
            </div>"""
        st.html(f'<div style="border:1px solid #00ff4120;padding:8px 10px;background:#00ff4105;">{recent_rows}</div>')

    st.html('<div class="sep"></div>')

    # ── AI Usage + Realtime Feed ─────────────────────────────────────────────
    col_ai, col_feed = st.columns(2)

    with col_ai:
        st.html('<div class="section-hdr">// AI USAGE</div>')
        if ai_stats:
            total_calls = ai_stats.get("total_calls", 0) or 0
            est_cost = ai_stats.get("estimated_cost_usd", 0) or 0
            in_tok = ai_stats.get("total_input_tokens", 0) or 0
            out_tok = ai_stats.get("total_output_tokens", 0) or 0
            model_name = ai_stats.get("model", "—")
            in_price = getattr(config, "AI_INPUT_PRICE_PER_MTOK", 0)
            out_price = getattr(config, "AI_OUTPUT_PRICE_PER_MTOK", 0)
            cards_ai = st.columns(2)
            with cards_ai[0]:
                st.html(neon_stat_card(
                    "TOTAL CALLS",
                    f"{total_calls:,}",
                    model_name,
                    "c-green",
                ))
            with cards_ai[1]:
                st.html(neon_stat_card(
                    "EST. COST",
                    f"${est_cost:.4f}",
                    f"in {in_tok:,} · out {out_tok:,} tok",
                    "c-amber",
                ))
        else:
            st.html(
                '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
                '// NO AI DATA</div>',
            )

    with col_feed:
        st.html('<div class="section-hdr">// REALTIME FEED</div>')
        if feed_status:
            connected = feed_status.get("connected", False)
            watched = feed_status.get("watched_assets", 0) or 0
            cache_size = feed_status.get("quote_cache_size", 0) or 0
            msg_count = feed_status.get("message_count", 0) or 0
            reconnects = feed_status.get("reconnect_count", 0) or 0
            max_assets = getattr(config, "REALTIME_MARKET_WS_MAX_ASSETS", "—")
            status_label = "CONNECTED" if connected else "DISCONNECTED"
            status_col = "c-green" if connected else "c-red"
            cards_feed = st.columns(2)
            with cards_feed[0]:
                st.html(neon_stat_card(
                    "FEED STATUS",
                    status_label,
                    f"{watched}/{max_assets} assets · {cache_size} cached",
                    status_col,
                ))
            with cards_feed[1]:
                st.html(neon_stat_card(
                    "WIRE TRAFFIC",
                    f"{msg_count:,}",
                    f"{reconnects} reconnect(s)",
                    "c-white",
                ))
        else:
            st.html(
                '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
                '// FEED OFFLINE</div>',
            )

# ---------------------------------------------------------------------------
# TAB: RISK (Task 7)
# ---------------------------------------------------------------------------

with tab_risk:
    # ── Gauge row ────────────────────────────────────────────────────────────
    g1, g2, g3 = st.columns(3)

    def _gauge_color(ratio: float) -> str:
        if ratio < 0.5:
            return C_PRIMARY
        if ratio < 0.8:
            return C_WARNING
        return C_DANGER

    # Max Drawdown gauge (all-time)
    mdd_max = config.DRAWDOWN_STOP_THRESHOLD * 100
    mdd_ratio = min(max_drawdown_pct / mdd_max, 1.0) if mdd_max > 0 else 0.0
    mdd_bar = _gauge_color(mdd_ratio)
    with g1:
        fig_mdd = go.Figure(go.Indicator(
            mode="gauge+number",
            value=max_drawdown_pct,
            number={"suffix": "%", "font": {"size": 28, "family": "Courier New, monospace", "color": mdd_bar}},
            title={"text": "MAX DRAWDOWN", "font": {"size": 11, "family": "Courier New, monospace", "color": "rgba(0,255,65,0.5)"}},
            gauge={
                "axis": {"range": [0, mdd_max], "tickfont": {"size": 9, "color": "rgba(0,255,65,0.38)"}},
                "bar": {"color": mdd_bar},
                "bgcolor": "rgba(0,255,65,0.06)",
                "steps": [
                    {"range": [0, mdd_max * 0.5], "color": "rgba(0,255,65,0.08)"},
                    {"range": [mdd_max * 0.5, mdd_max * 0.8], "color": "rgba(255,170,0,0.08)"},
                    {"range": [mdd_max * 0.8, mdd_max], "color": "rgba(255,0,68,0.08)"},
                ],
                "threshold": {"line": {"color": C_DANGER, "width": 2}, "thickness": 0.75, "value": mdd_max * 0.8},
            },
        ))
        fig_mdd.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            height=200, margin={"l": 20, "r": 20, "t": 40, "b": 10},
        )
        st.plotly_chart(fig_mdd, width="stretch", key="gauge_mdd")
        if max_drawdown_pct >= mdd_max * 0.8:
            mdd_status = f'<div style="text-align:center;font-size:0.6rem;color:{C_DANGER};">⚠ CRITICAL</div>'
        elif max_drawdown_pct >= mdd_max * 0.5:
            mdd_status = f'<div style="text-align:center;font-size:0.6rem;color:{C_WARNING};">⚠ ELEVATED</div>'
        else:
            mdd_status = f'<div style="text-align:center;font-size:0.6rem;color:{C_PRIMARY};">✓ HEALTHY</div>'
        st.html(mdd_status)

    # Win Rate gauge (all-time)
    wr_bar = C_DANGER if win_rate < 30 else (C_WARNING if win_rate < 50 else C_PRIMARY)
    with g2:
        fig_wr = go.Figure(go.Indicator(
            mode="gauge+number",
            value=win_rate,
            number={"suffix": "%", "font": {"size": 28, "family": "Courier New, monospace", "color": wr_bar}},
            title={"text": "WIN RATE", "font": {"size": 11, "family": "Courier New, monospace", "color": "rgba(0,255,65,0.5)"}},
            gauge={
                "axis": {"range": [0, 100], "tickfont": {"size": 9, "color": "rgba(0,255,65,0.38)"}},
                "bar": {"color": wr_bar},
                "bgcolor": "rgba(0,255,65,0.06)",
                "steps": [
                    {"range": [0, 30], "color": "rgba(255,0,68,0.08)"},
                    {"range": [30, 50], "color": "rgba(255,170,0,0.08)"},
                    {"range": [50, 100], "color": "rgba(0,255,65,0.08)"},
                ],
                "threshold": {"line": {"color": C_WARNING, "width": 2}, "thickness": 0.75, "value": 50},
            },
        ))
        fig_wr.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            height=200, margin={"l": 20, "r": 20, "t": 40, "b": 10},
        )
        st.plotly_chart(fig_wr, width="stretch", key="gauge_wr")
        wr_status = f'<div style="text-align:center;font-size:0.6rem;color:{wr_bar};">{winning}W / {total_trades - winning}L</div>'
        st.html(wr_status)

    # Max Loss Streak gauge (all-time)
    mls_max = float(config.CONSECUTIVE_LOSS_PAUSE) if config.CONSECUTIVE_LOSS_PAUSE > 0 else 5.0
    mls_ratio = min(max_loss_streak / mls_max, 1.0)
    mls_bar = _gauge_color(mls_ratio)
    with g3:
        fig_mls = go.Figure(go.Indicator(
            mode="gauge+number",
            value=float(max_loss_streak),
            number={"suffix": "", "font": {"size": 28, "family": "Courier New, monospace", "color": mls_bar}},
            title={"text": "MAX LOSS STREAK", "font": {"size": 11, "family": "Courier New, monospace", "color": "rgba(0,255,65,0.5)"}},
            gauge={
                "axis": {"range": [0, mls_max], "tickfont": {"size": 9, "color": "rgba(0,255,65,0.38)"}},
                "bar": {"color": mls_bar},
                "bgcolor": "rgba(0,255,65,0.06)",
                "steps": [
                    {"range": [0, mls_max * 0.5], "color": "rgba(0,255,65,0.08)"},
                    {"range": [mls_max * 0.5, mls_max * 0.8], "color": "rgba(255,170,0,0.08)"},
                    {"range": [mls_max * 0.8, mls_max], "color": "rgba(255,0,68,0.08)"},
                ],
                "threshold": {"line": {"color": C_DANGER, "width": 2}, "thickness": 0.75, "value": mls_max * 0.8},
            },
        ))
        fig_mls.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            height=200, margin={"l": 20, "r": 20, "t": 40, "b": 10},
        )
        st.plotly_chart(fig_mls, width="stretch", key="gauge_mls")
        if cons_wins > 0:
            mls_status = f'<div style="text-align:center;font-size:0.6rem;color:{C_PRIMARY};">NOW: W{cons_wins}</div>'
        elif cons_losses > 0:
            mls_status = f'<div style="text-align:center;font-size:0.6rem;color:{C_WARNING};">NOW: L{cons_losses}</div>'
        else:
            mls_status = f'<div style="text-align:center;font-size:0.6rem;color:#00ff4160;">NOW: —</div>'
        st.html(mls_status)

    st.html('<hr style="border:none;border-top:1px solid #00ff4120;margin:20px 0;">')

    # ── Risk Detail Cards ─────────────────────────────────────────────────────
    d1, d2, d3 = st.columns(3)
    _card_style = (
        "border:1px solid #00ff4120;border-radius:4px;padding:14px 16px;"
        "background:rgba(0,255,65,0.03);font-family:'Courier New',monospace;"
    )
    _lbl_style = "color:#00ff4160;font-size:0.6rem;"
    _val_style = "color:#cccccc;font-size:0.75rem;font-weight:bold;"

    def _detail_row(label: str, value: str) -> str:
        return (
            f'<div style="display:flex;justify-content:space-between;'
            f'border-bottom:1px dotted #00ff4115;padding:5px 0;">'
            f'<span style="{_lbl_style}">{label}</span>'
            f'<span style="{_val_style}">{value}</span></div>'
        )

    dl_limit = limit_pct if limit_pct > 0 else 100.0
    with d1:
        headroom_pct = max(dl_limit - daily_loss, 0.0)
        st.html(
            f'<div style="{_card_style}">'
            f'<div style="color:{C_PRIMARY};font-size:0.65rem;margin-bottom:10px;">// DAILY LOSS LIMIT</div>'
            + _detail_row("REALIZED LOSS", f"{daily_loss:.2f}%")
            + _detail_row("LIMIT", f"{dl_limit:.1f}%")
            + _detail_row("HEADROOM", f"{headroom_pct:.2f}%")
            + '</div>',
        )

    with d2:
        st.html(
            f'<div style="{_card_style}">'
            f'<div style="color:{C_PRIMARY};font-size:0.65rem;margin-bottom:10px;">// DRAWDOWN CONTROL</div>'
            + _detail_row("CURRENT DD", f"{dd:.2f}%")
            + _detail_row("HALVE BETS AT", f"{config.DRAWDOWN_REDUCE_THRESHOLD * 100:.1f}%")
            + _detail_row("STOP AT", f"{config.DRAWDOWN_STOP_THRESHOLD * 100:.1f}%")
            + '</div>',
        )

    with d3:
        st.html(
            f'<div style="{_card_style}">'
            f'<div style="color:{C_PRIMARY};font-size:0.65rem;margin-bottom:10px;">// LOSS STREAK CONTROL</div>'
            + _detail_row("CURRENT STREAK", f"L{cons_losses}" if cons_losses > 0 else (f"W{cons_wins}" if cons_wins > 0 else "—"))
            + _detail_row("HALVE BETS AT", f"{config.CONSECUTIVE_LOSS_REDUCE} losses")
            + _detail_row("PAUSE AT", f"{config.CONSECUTIVE_LOSS_PAUSE} losses")
            + '</div>',
        )

# ---------------------------------------------------------------------------
# TAB: CONFIG (Task 7)
# ---------------------------------------------------------------------------

with tab_config:
    st.html('<div class="section-hdr">// CONFIGURATION</div>')

    def cfg_row(label: str, val: str) -> str:
        return (
            f'<div style="display:flex;justify-content:space-between;'
            f'border-bottom:1px dotted #00ff4115;padding:4px 0;font-size:0.65rem;">'
            f'<span style="color:#00ff4160;">{label}</span>'
            f'<span style="color:#cccccc;">{val}</span></div>'
        )

    _panel_style = (
        "border:1px solid #00ff4120;border-radius:4px;padding:14px 16px;"
        "background:rgba(0,255,65,0.03);font-family:'Courier New',monospace;"
    )
    _panel_hdr = "color:#00ff41;font-size:0.65rem;margin-bottom:10px;"

    cc1, cc2, cc3 = st.columns(3)

    with cc1:
        st.html(
            f'<div style="{_panel_style}">'
            f'<div style="{_panel_hdr}">// TRADING</div>'
            + cfg_row("Min Edge", f"{config.MIN_EDGE_PCT:.1f}%")
            + cfg_row("AI Min Edge", f"{config.AI_MIN_EDGE_PCT:.1f}%")
            + cfg_row("AI Confidence", f"{config.MIN_AI_CONFIDENCE * 100:.0f}%")
            + cfg_row("Bet Size", f"{config.BET_SIZE_PCT:.2f}%")
            + cfg_row("Max Bet ($)", f"${config.MAX_BET_SIZE:,.0f}")
            + cfg_row("Max Exposure", f"{config.MAX_EXPOSURE_PCT:.1f}%")
            + cfg_row("Min Price", f"{config.ODDS_COMPARISON_MIN_PRICE:.2f}")
            + cfg_row("Min Bookmakers", str(config.MIN_BOOKMAKER_COUNT))
            + '</div>',
        )

    with cc2:
        st.html(
            f'<div style="{_panel_style}">'
            f'<div style="{_panel_hdr}">// RISK</div>'
            + cfg_row("Daily Loss Limit (%)", f"{config.DAILY_LOSS_LIMIT_PCT * 100:.1f}%")
            + cfg_row("Drawdown Reduce (%)", f"{config.DRAWDOWN_REDUCE_THRESHOLD * 100:.1f}%")
            + cfg_row("Drawdown Stop (%)", f"{config.DRAWDOWN_STOP_THRESHOLD * 100:.1f}%")
            + cfg_row("Consec. Loss Reduce", str(config.CONSECUTIVE_LOSS_REDUCE))
            + cfg_row("Consec. Loss Pause", str(config.CONSECUTIVE_LOSS_PAUSE))
            + cfg_row("Pause Duration (min)", str(config.PAUSE_DURATION_MINUTES))
            + '</div>',
        )

    with cc3:
        _ai_model_short = config.AI_MODEL.split("/")[-1] if "/" in config.AI_MODEL else config.AI_MODEL
        if len(_ai_model_short) > 20:
            _ai_model_short = _ai_model_short[:18] + ".."
        st.html(
            f'<div style="{_panel_style}">'
            f'<div style="{_panel_hdr}">// OPERATIONAL</div>'
            + cfg_row("Poll Interval (s)", str(config.POLL_INTERVAL))
            + cfg_row("AI Model", _ai_model_short)
            + cfg_row("Paper Trading", "YES" if config.PAPER_TRADING else "NO")
            + cfg_row("Min Volume 24h ($)", f"${config.MIN_VOLUME_24H:,.0f}")
            + cfg_row("Min Liquidity ($)", f"${config.MIN_LIQUIDITY:,.0f}")
            + cfg_row("Realtime Feed", "ON" if config.REALTIME_MARKET_WS_ENABLED else "OFF")
            + cfg_row("Realtime Gate", "ON" if config.ENABLE_REALTIME_EXECUTION_GATE else "OFF")
            + cfg_row("Max Spread", f"{config.REALTIME_GATE_MAX_SPREAD:.4f}")
            + cfg_row("Min Depth ($)", f"${config.REALTIME_GATE_MIN_DEPTH_USD:,.0f}")
            + '</div>',
        )

# ---------------------------------------------------------------------------
# TAB: BTC 5M
# ---------------------------------------------------------------------------

with tab_btc:
    # --- Live Signal Panel ---
    if btc_signal:
        sig_state = btc_signal.get("state", "UNKNOWN")
        sig_btc = btc_signal.get("btc_price")
        sig_strike = btc_signal.get("strike_price")
        sig_market = btc_signal.get("market", {})
        sig_data = btc_signal.get("signal", {})
        sig_connected = btc_signal.get("rtds_connected", False)

        # State badge
        state_colors = {
            "IDLE": ("pill-yellow", "SCANNING"),
            "WAITING": ("pill-yellow", "WAITING FOR WINDOW"),
            "OBSERVING": ("pill-green", "OBSERVING"),
            "TRADING": ("pill-green", "POSITION OPEN"),
            "RESOLVING": ("pill-yellow", "RESOLVING"),
        }
        pill_cls, pill_label = state_colors.get(sig_state, ("pill-red", sig_state))
        conn_icon = "RTDS OK" if sig_connected else "RTDS DOWN"
        st.html(
            f'<div class="status-pill {pill_cls}">'
            f'<span class="dot"></span>BTC BOT: {pill_label} — {conn_icon}'
            f'</div>'
        )

        # Live price cards
        lc1, lc2, lc3, lc4 = st.columns(4)
        with lc1:
            st.html(neon_stat_card(
                "BTC PRICE",
                f"${sig_btc:,.2f}" if sig_btc else "—",
                f"RTDS msgs: {btc_signal.get('rtds_msgs', 0)}",
                "c-amber",
            ))
        with lc2:
            if sig_strike:
                diff = sig_btc - sig_strike if sig_btc else 0
                direction = "ABOVE" if diff >= 0 else "BELOW"
                st.html(neon_stat_card(
                    "STRIKE",
                    f"${sig_strike:,.2f}",
                    f"${abs(diff):,.2f} {direction}",
                    "c-green" if diff >= 0 else "c-red",
                ))
            else:
                st.html(neon_stat_card("STRIKE", "—", "waiting for window", "c-white"))
        with lc3:
            if sig_data:
                edge = sig_data.get("edge_pct", 0)
                side = sig_data.get("side", "—")
                st.html(neon_stat_card(
                    f"SIGNAL: {side}",
                    f"{edge:+.1f}%",
                    f"conf={sig_data.get('confidence', 0):.0%}  vol={sig_data.get('volatility', 0):.0%}",
                    "c-green" if edge >= 3 else "c-amber" if edge > 0 else "c-red",
                ))
            else:
                st.html(neon_stat_card("SIGNAL", "—", "no data yet", "c-white"))
        with lc4:
            remaining = sig_market.get("time_remaining_sec", 0)
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            q = sig_market.get("question", "—")[:35] if sig_market else "—"
            st.html(neon_stat_card(
                "WINDOW",
                f"{mins}:{secs:02d}" if remaining > 0 else "—",
                q,
                "c-green" if remaining > 60 else "c-amber" if remaining > 0 else "c-white",
            ))

        # Signal detail table
        if sig_data:
            st.html('<div class="sep"></div>')
            st.html('<div class="section-hdr">// SIGNAL ENGINE</div>')

            p_up = sig_data.get("statistical_prob", 0.5)
            p_down = 1.0 - p_up
            mom = sig_data.get("momentum_adj", 0)
            flow = sig_data.get("orderflow_adj", 0)
            model_p = sig_data.get("model_probability", 0.5)
            mkt_p = sig_data.get("market_price", 0.5)
            up_mkt = sig_market.get("up_price", 0.5)
            down_mkt = sig_market.get("down_price", 0.5)

            st.html(
                '<div class="data-table-wrap"><table class="data-table">'
                '<tr><th>LAYER</th><th>VALUE</th><th>DESCRIPTION</th></tr>'
                f'<tr><td style="color:#f7931a">Statistical</td>'
                f'<td class="c-green">{p_up:.1%} UP / {p_down:.1%} DOWN</td>'
                f'<td style="color:#00ff4160">Gaussian model (75% weight)</td></tr>'
                f'<tr><td style="color:#f7931a">Momentum</td>'
                f'<td class="{"c-green" if mom > 0 else "c-red" if mom < 0 else "c-white"}">{mom:+.2%}</td>'
                f'<td style="color:#00ff4160">60s price slope (15% weight)</td></tr>'
                f'<tr><td style="color:#f7931a">Order Flow</td>'
                f'<td class="{"c-green" if flow > 0 else "c-red" if flow < 0 else "c-white"}">{flow:+.2%}</td>'
                f'<td style="color:#00ff4160">Market consensus (10% weight)</td></tr>'
                '<tr><td colspan="3" style="border-top:1px solid #00ff4120"></td></tr>'
                f'<tr><td style="color:#fff">Model</td>'
                f'<td class="c-green" style="font-weight:bold">{model_p:.1%}</td>'
                f'<td style="color:#00ff4160">Combined P({sig_data.get("side","?")})</td></tr>'
                f'<tr><td style="color:#fff">Market</td>'
                f'<td class="c-amber">{mkt_p:.1%}</td>'
                f'<td style="color:#00ff4160">Polymarket: Up={up_mkt:.2f} Down={down_mkt:.2f}</td></tr>'
                '</table></div>'
            )

        st.html('<div class="sep"></div>')

    elif not btc_data:
        st.html(
            '<div style="color:#00ff4140;font-size:0.8rem;padding:40px 0;text-align:center">'
            '// BTC BOT NOT ACTIVE — NO DATA YET<br>'
            '<span style="font-size:0.65rem">Start: python -m btc.main_btc</span></div>'
        )

    if btc_data:
        btc_bankroll = btc_data.get("current_bankroll", 0)
        btc_starting = btc_data.get("starting_bankroll", 0)
        btc_peak = btc_data.get("peak_bankroll", 0)
        btc_day_start = btc_data.get("day_start_bankroll", 0)
        btc_open = btc_data.get("open_positions", {})
        btc_history = btc_data.get("trade_history", [])
        btc_bankroll_hist = btc_data.get("bankroll_history", [])

        btc_open_cost = sum(p.get("cost_basis", 0) for p in btc_open.values())
        btc_realized = btc_bankroll + btc_open_cost
        btc_total_pnl = btc_realized - btc_starting
        btc_roi = (btc_realized - btc_starting) / btc_starting * 100 if btc_starting > 0 else 0.0
        btc_daily_pnl = btc_realized - btc_day_start if btc_day_start > 0 else 0.0
        btc_total_trades = len(btc_history) + len(btc_open)
        btc_wins = sum(1 for t in btc_history if (t.get("pnl") or 0) > 0)
        btc_win_rate = (btc_wins / len(btc_history) * 100) if btc_history else 0.0

        # --- Hero Cards ---
        st.html('<div class="section-hdr">// BTC 5-MINUTE BOT</div>')

        b1, b2, b3, b4 = st.columns(4)

        with b1:
            st.html(neon_stat_card(
                "BTC EQUITY",
                f"${btc_realized:,.2f}",
                f"{fmt_usd(btc_total_pnl)} ({btc_roi:+.1f}% ROI)",
                pnl_color(btc_total_pnl),
            ))

        with b2:
            st.html(neon_stat_card(
                "TODAY P&L",
                fmt_usd(btc_daily_pnl),
                f"from ${btc_day_start:,.2f}",
                pnl_color(btc_daily_pnl),
            ))

        with b3:
            st.html(neon_stat_card(
                "WIN RATE",
                f"{btc_win_rate:.1f}%",
                f"{btc_total_trades} trades ({btc_wins}W/{len(btc_history) - btc_wins}L)",
                "c-amber" if btc_win_rate >= 50 else "c-red",
            ))

        with b4:
            st.html(neon_stat_card(
                "OPEN",
                f"{len(btc_open)}",
                f"${btc_open_cost:,.2f} exposed",
                "c-white",
            ))

        st.html('<div class="sep"></div>')

        # --- Equity Curve ---
        if btc_bankroll_hist:
            st.html('<div class="section-hdr">// BTC EQUITY CURVE</div>')
            btc_eq = pd.DataFrame(btc_bankroll_hist)
            btc_eq["timestamp"] = pd.to_datetime(btc_eq["timestamp"])
            btc_eq = btc_eq.sort_values("timestamp")

            fig_btc = go.Figure()
            fig_btc.add_trace(go.Scatter(
                x=btc_eq["timestamp"],
                y=btc_eq["bankroll"],
                mode="lines",
                line=dict(color="#f7931a", width=2),
                fill="tozeroy",
                fillcolor="rgba(247,147,26,0.08)",
                hovertemplate="$%{y:,.2f}<br>%{x|%b %d %H:%M}<extra></extra>",
            ))

            if btc_history:
                for trade in btc_history[-30:]:
                    closed_at = trade.get("closed_at", "")
                    pnl_val = trade.get("pnl", 0) or 0
                    if not closed_at:
                        continue
                    try:
                        trade_time = pd.to_datetime(closed_at)
                    except Exception:
                        continue
                    idx = btc_eq["timestamp"].searchsorted(trade_time)
                    if idx >= len(btc_eq):
                        idx = len(btc_eq) - 1
                    bankroll_at = btc_eq.iloc[idx]["bankroll"]
                    marker_color = "#00ff41" if pnl_val >= 0 else "#ff0044"
                    fig_btc.add_trace(go.Scatter(
                        x=[trade_time],
                        y=[bankroll_at],
                        mode="markers",
                        marker=dict(color=marker_color, size=7, symbol="diamond",
                                    line=dict(width=1, color=marker_color)),
                        hovertemplate=f"P&L: {fmt_usd(pnl_val)}<extra></extra>",
                        showlegend=False,
                    ))

            fig_btc.update_layout(
                **plotly_theme(),
                height=220,
                showlegend=False,
                yaxis_tickprefix="$",
            )
            st.plotly_chart(fig_btc, width="stretch", config={"displayModeBar": False})

        st.html('<div class="sep"></div>')

        # --- Open Positions ---
        if btc_open:
            st.html('<div class="section-hdr">// OPEN POSITIONS</div>')
            rows = ""
            for pid, pos in btc_open.items():
                side = pos.get("side", "?")
                q = html_esc(pos.get("question", "?")[:50])
                entry = pos.get("entry_price", 0)
                shares = pos.get("size", 0)
                cost = pos.get("cost_basis", 0)
                max_pnl = shares - cost
                end_dt = pos.get("end_date", "")
                time_left = fmt_end_window(end_dt) if end_dt else "—"
                side_badge = (
                    '<span style="color:#00ff41;font-weight:bold">UP</span>'
                    if "yes" in side.lower() or "up" in side.lower()
                    else '<span style="color:#ff0044;font-weight:bold">DOWN</span>'
                )
                rows += (
                    f'<tr>'
                    f'<td style="color:#ccc">{q}</td>'
                    f'<td>{side_badge}</td>'
                    f'<td>${entry:.2f}</td>'
                    f'<td>{shares:.1f}</td>'
                    f'<td>${cost:.2f}</td>'
                    f'<td class="{pnl_color(max_pnl)}">{fmt_usd(max_pnl)}</td>'
                    f'<td style="color:#00ff4160">{time_left}</td>'
                    f'</tr>'
                )

            st.html(
                '<div class="data-table-wrap"><table class="data-table">'
                '<tr><th>MARKET</th><th>SIDE</th><th>ENTRY</th><th>SHARES</th>'
                '<th>COST</th><th>MAX P&L</th><th>ENDS</th></tr>'
                + rows + '</table></div>'
            )
        else:
            st.html(
                '<div style="color:#00ff4140;font-size:0.7rem;padding:15px 0;text-align:center">'
                '// NO OPEN BTC POSITIONS</div>'
            )

        # --- Trade History ---
        if btc_history:
            st.html('<div class="section-hdr">// TRADE HISTORY</div>')
            rows = ""
            for trade in reversed(btc_history[-50:]):
                pnl_val = trade.get("pnl", 0) or 0
                exit_p = trade.get("exit_price")
                if exit_p is not None and 0.01 < exit_p < 0.99:
                    badge = f'<span class="badge badge-amber">@{exit_p:.2f}</span>'
                elif pnl_val > 0:
                    badge = '<span class="badge badge-green">WIN</span>'
                else:
                    badge = '<span class="badge badge-red">LOSS</span>'

                q = html_esc(trade.get("question", "?")[:45])
                entry = trade.get("entry_price", 0)
                exit_v = exit_p if exit_p is not None else 0
                closed = trade.get("closed_at", "")
                try:
                    closed_fmt = parse_iso(closed).strftime("%m/%d %H:%M") if closed else "—"
                except Exception:
                    closed_fmt = "—"

                rows += (
                    f'<tr>'
                    f'<td>{badge}</td>'
                    f'<td style="color:#ccc">{q}</td>'
                    f'<td>${entry:.2f}</td>'
                    f'<td>${exit_v:.2f}</td>'
                    f'<td class="{pnl_color(pnl_val)}">{fmt_usd(pnl_val)}</td>'
                    f'<td style="color:#00ff4160">{closed_fmt}</td>'
                    f'</tr>'
                )

            st.html(
                '<div class="data-table-wrap"><table class="data-table">'
                '<tr><th></th><th>MARKET</th><th>ENTRY</th><th>EXIT</th>'
                '<th>P&L</th><th>CLOSED</th></tr>'
                + rows + '</table></div>'
            )

        # --- P&L Distribution ---
        if btc_history:
            st.html('<div class="section-hdr">// P&L DISTRIBUTION</div>')
            pnls = [t.get("pnl", 0) or 0 for t in btc_history]
            fig_pnl = go.Figure()
            colors = ["#00ff41" if p >= 0 else "#ff0044" for p in pnls]
            fig_pnl.add_trace(go.Bar(
                x=list(range(1, len(pnls) + 1)),
                y=pnls,
                marker_color=colors,
                hovertemplate="Trade %{x}<br>P&L: $%{y:,.2f}<extra></extra>",
            ))
            fig_pnl.update_layout(
                **plotly_theme(),
                height=180,
                showlegend=False,
                yaxis_tickprefix="$",
                xaxis_title="Trade #",
            )
            st.plotly_chart(fig_pnl, width="stretch", config={"displayModeBar": False})

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.html(
    f'<div class="footer">MeQ0L15 · POLYMARKET WAR MACHINE · AUTO-REFRESH {REFRESH_SECONDS}s · {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>',
)

time.sleep(REFRESH_SECONDS)
st.rerun()
