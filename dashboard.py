"""
Polymarket Bot — Streamlit Dashboard (Cyberpunk Theme)
Auto-refreshes every 10 seconds.
Run: streamlit run dashboard.py
"""

import json
import time
from datetime import datetime, timezone
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
        font=dict(family="Courier New, monospace", color="#00ff4160", size=10),
        margin=dict(l=40, r=20, t=30, b=30),
        xaxis=dict(
            gridcolor="#00ff4110",
            zerolinecolor="#00ff4120",
            tickfont=dict(color="#00ff4140", size=9),
        ),
        yaxis=dict(
            gridcolor="#00ff4110",
            zerolinecolor="#00ff4120",
            tickfont=dict(color="#00ff4140", size=9),
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
""", unsafe_allow_html=True)

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
# Status banner
# ---------------------------------------------------------------------------

if paused:
    st.markdown(f'<div class="status-pill pill-yellow"><span class="dot"></span>PAUSED — CONSECUTIVE LOSSES: {cons_losses}</div>', unsafe_allow_html=True)
elif blocked_daily:
    st.markdown(f'<div class="status-pill pill-red"><span class="dot"></span>BLOCKED — DAILY LOSS LIMIT {daily_loss:.1f}% / {limit_pct:.0f}%</div>', unsafe_allow_html=True)
elif blocked_drawdown:
    st.markdown(f'<div class="status-pill pill-red"><span class="dot"></span>BLOCKED — MAX DRAWDOWN {dd:.1f}%</div>', unsafe_allow_html=True)
elif blocked_cons:
    st.markdown(f'<div class="status-pill pill-yellow"><span class="dot"></span>PAUSING — {cons_losses} CONSECUTIVE LOSSES</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="status-pill pill-green"><span class="dot"></span>BOT ACTIVE — SCANNING MARKETS</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_overview, tab_positions, tab_analytics, tab_risk, tab_config = st.tabs([
    "[ OVERVIEW ]", "[ POSITIONS ]", "[ ANALYTICS ]", "[ RISK ]", "[ CONFIG ]"
])

# ---------------------------------------------------------------------------
# TAB: OVERVIEW
# ---------------------------------------------------------------------------

with tab_overview:
    # Hero stat cards
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(neon_stat_card(
            "TOTAL EQUITY",
            f"${realized_bankroll:,.2f}",
            f"{fmt_usd(total_pnl)} all-time",
            pnl_color(total_pnl),
        ), unsafe_allow_html=True)

    with c2:
        daily_pct = abs(daily_pnl) / day_start * 100 if day_start > 0 else 0
        st.markdown(neon_stat_card(
            "TODAY P&L",
            fmt_usd(daily_pnl),
            f"{fmt_pct(daily_pct if daily_pnl >= 0 else -daily_pct)} today",
            pnl_color(daily_pnl),
        ), unsafe_allow_html=True)

    with c3:
        streak = f"W{cons_wins}" if cons_wins > 0 else (f"L{cons_losses}" if cons_losses > 0 else "—")
        st.markdown(neon_stat_card(
            "WIN RATE",
            f"{win_rate:.1f}%",
            f"{total_trades} trades · {streak}",
            "c-amber",
        ), unsafe_allow_html=True)

    with c4:
        st.markdown(neon_stat_card(
            "OPEN EXPOSURE",
            f"${open_cost:,.2f}",
            f"{len(open_pos)} positions active",
            "c-white",
        ), unsafe_allow_html=True)

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    # Equity Curve Chart
    st.markdown('<div class="section-hdr">// EQUITY CURVE</div>', unsafe_allow_html=True)

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
                        f"{trade.get('question', '')[:40]}<br>"
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
        st.plotly_chart(fig_equity, use_container_width=True, config={"displayModeBar": False})
    else:
        st.markdown(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO EQUITY DATA YET — SNAPSHOTS RECORDED EACH SAVE CYCLE</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    # Bottom row: Risk mini + Recent trades
    bot_left, bot_right = st.columns(2)

    with bot_left:
        st.markdown('<div class="section-hdr">// RISK STATUS</div>', unsafe_allow_html=True)

        dl_ratio = min(daily_loss / limit_pct, 1.0) if limit_pct > 0 else 0.0
        dl_color = C_DANGER if daily_loss >= limit_pct * 0.8 else (C_WARNING if daily_loss >= limit_pct * 0.5 else C_PRIMARY)
        st.markdown(f'''
        <div style="display:flex;justify-content:space-between;font-size:0.6rem;color:{dl_color};margin-bottom:4px;">
          <span>DAILY LOSS</span><span>{daily_loss:.1f}% / {limit_pct:.0f}%</span>
        </div>
        <div style="height:4px;background:#00ff4115;border-radius:2px;overflow:hidden;margin-bottom:12px;">
          <div style="width:{dl_ratio*100:.0f}%;height:100%;background:{dl_color};box-shadow:0 0 6px {dl_color};border-radius:2px;"></div>
        </div>''', unsafe_allow_html=True)

        dd_stop = config.DRAWDOWN_STOP_THRESHOLD * 100
        dd_ratio = min(dd / dd_stop, 1.0) if dd_stop > 0 else 0.0
        dd_color = C_DANGER if dd >= config.DRAWDOWN_REDUCE_THRESHOLD * 100 else (C_WARNING if dd >= dd_stop * 0.5 else C_PRIMARY)
        st.markdown(f'''
        <div style="display:flex;justify-content:space-between;font-size:0.6rem;color:{dd_color};margin-bottom:4px;">
          <span>DRAWDOWN</span><span>{dd:.1f}% / {dd_stop:.0f}%</span>
        </div>
        <div style="height:4px;background:#ffaa0015;border-radius:2px;overflow:hidden;margin-bottom:12px;">
          <div style="width:{dd_ratio*100:.0f}%;height:100%;background:{dd_color};box-shadow:0 0 6px {dd_color};border-radius:2px;"></div>
        </div>''', unsafe_allow_html=True)

        pause_at = config.CONSECUTIVE_LOSS_PAUSE
        cl_ratio = min(cons_losses / pause_at, 1.0) if pause_at > 0 else 0.0
        cl_color = C_DANGER if cons_losses >= pause_at * 0.7 else C_PRIMARY
        st.markdown(f'''
        <div style="display:flex;justify-content:space-between;font-size:0.6rem;color:{cl_color};margin-bottom:4px;">
          <span>LOSS STREAK</span><span>{cons_losses} / {pause_at}</span>
        </div>
        <div style="height:4px;background:#00ff4115;border-radius:2px;overflow:hidden;">
          <div style="width:{cl_ratio*100:.0f}%;height:100%;background:{cl_color};box-shadow:0 0 6px {cl_color};border-radius:2px;"></div>
        </div>''', unsafe_allow_html=True)

    with bot_right:
        st.markdown('<div class="section-hdr">// RECENT TRADES</div>', unsafe_allow_html=True)

        if history:
            recent = list(reversed(history))[:5]
            rows_html = ""
            for t in recent:
                pnl_val = t.get("pnl", 0) or 0
                pc = "color:#00ff41" if pnl_val >= 0 else "color:#ff0044"
                market = t.get("question", "")[:35]
                side = t.get("side", "")
                side_color = "#00ff41" if side == "YES" else "#ff0044"
                rows_html += f'''
                <div style="display:flex;justify-content:space-between;padding:3px 0;font-size:0.65rem;">
                  <span style="color:#ccc;flex:2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{market}</span>
                  <span style="color:{side_color};flex:0.4;text-align:center;">{side}</span>
                  <span style="{pc};flex:0.6;text-align:right;">{fmt_usd(pnl_val)}</span>
                </div>'''
            st.markdown(f'''
            <div style="border:1px solid #00ff4120;padding:10px;background:#00ff4105;">
              <div style="display:flex;justify-content:space-between;font-size:0.55rem;color:#00ff4140;border-bottom:1px solid #00ff4110;padding-bottom:4px;margin-bottom:4px;">
                <span style="flex:2;">MARKET</span><span style="flex:0.4;text-align:center;">SIDE</span><span style="flex:0.6;text-align:right;">P&L</span>
              </div>
              {rows_html}
            </div>''', unsafe_allow_html=True)
        else:
            st.markdown(
                '<div style="color:#00ff4140;font-size:0.65rem;padding:12px 0">// NO CLOSED TRADES YET</div>',
                unsafe_allow_html=True,
            )

# ---------------------------------------------------------------------------
# TAB: POSITIONS (Task 5)
# ---------------------------------------------------------------------------

with tab_positions:
    # --- Open Positions Table ---
    st.markdown(f'<div class="section-hdr">// OPEN POSITIONS [{len(open_pos)}]</div>', unsafe_allow_html=True)

    if open_pos:
        rows_html = ""
        for pos_id, pos in open_pos.items():
            question = pos.get("question", "")[:50]
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
            max_pnl = shares * (1.0 - entry) if side == "YES" else shares * entry
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
        st.markdown(f"""
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
        """, unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO OPEN POSITIONS</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    # --- Trade History Table ---
    st.markdown(f'<div class="section-hdr">// TRADE HISTORY [{len(history)} CLOSED]</div>', unsafe_allow_html=True)

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
            question = pos.get("question", "")[:50]
            side = pos.get("side", "")
            side_color = C_PRIMARY if side == "YES" else C_DANGER
            entry = pos.get("entry_price", 0)
            pnl_color = C_PRIMARY if pnl_val >= 0 else C_DANGER
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
              <td style="padding:7px 8px;text-align:right;color:{pnl_color};text-shadow:0 0 6px {pnl_color}80;">{fmt_usd(pnl_val)}</td>
              <td style="padding:7px 8px;text-align:right;color:#666;">{closed_str}</td>
              <td style="padding:7px 8px;text-align:center;"><a href="{url}" target="_blank" style="color:#00ff4180;text-decoration:none;font-size:0.75rem;">&#8599;</a></td>
            </tr>"""
        st.markdown(f"""
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
        """, unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO CLOSED TRADES YET</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    # --- P&L Distribution Chart ---
    st.markdown('<div class="section-hdr">// P&L DISTRIBUTION</div>', unsafe_allow_html=True)

    if history:
        pnl_values = [p.get("pnl", 0) or 0 for p in history]
        bar_colors = [C_PRIMARY if v >= 0 else C_DANGER for v in pnl_values]
        hover_texts = [
            f"{p.get('question', '')[:40]}<br>{fmt_usd(p.get('pnl', 0) or 0)}"
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
                "line": {"color": "#ffffff25", "width": 1},
            }],
            "yaxis": {**layout.get("yaxis", {}), "tickprefix": "$"},
            "xaxis": {**layout.get("xaxis", {}), "showticklabels": False},
        })
        fig_pnl.update_layout(**layout)
        st.plotly_chart(fig_pnl, use_container_width=True, config={"displayModeBar": False})
    else:
        st.markdown(
            '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
            '// NO TRADE DATA</div>',
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# TAB: ANALYTICS (Task 6)
# ---------------------------------------------------------------------------

with tab_analytics:
    st.markdown('<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">// ANALYTICS TAB — COMING SOON</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# TAB: RISK (Task 7)
# ---------------------------------------------------------------------------

with tab_risk:
    st.markdown('<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">// RISK TAB — COMING SOON</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# TAB: CONFIG (Task 7)
# ---------------------------------------------------------------------------

with tab_config:
    st.markdown('<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">// CONFIG TAB — COMING SOON</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown(
    f'<div class="footer">MeQ0L15 · POLYMARKET WAR MACHINE · AUTO-REFRESH {REFRESH_SECONDS}s · {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>',
    unsafe_allow_html=True,
)

time.sleep(REFRESH_SECONDS)
st.rerun()
