# Dashboard Cyberpunk Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Streamlit dashboard with a cyberpunk/hacker-themed UI featuring tabbed navigation, Plotly charts (equity curve, P&L bars, strategy donut, risk gauges), and custom HTML tables with neon glow effects.

**Architecture:** Single-file Streamlit app rewrite (`dashboard.py`) with Plotly for all charts, custom CSS for cyberpunk theme (scanlines, neon glow, monospace), and `st.tabs()` for 5-tab navigation. One backend change: append `bankroll_history` snapshots in `portfolio.py` to power the equity curve.

**Tech Stack:** Python, Streamlit (`st.tabs`, `st.markdown`, `st.plotly_chart`), Plotly (`plotly.graph_objects`), Pandas

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `dashboard.py` | Rewrite | Full cyberpunk dashboard — CSS theme, header, status banner, 5 tabs with all content |
| `portfolio.py` | Modify (lines 111-123, 372-380, 382-410) | Add `bankroll_history` field to `PortfolioState`, append on save, trim rolling window |
| `config.py` | Modify (line 154) | Add `BANKROLL_HISTORY_MAX_ENTRIES` setting |
| `requirements.txt` | Modify | Add `plotly>=5.18.0` |

---

## Task 1: Add `plotly` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add plotly to requirements.txt**

Add `plotly>=5.18.0` after the existing `pandas` line in `requirements.txt`:

```
py-clob-client>=0.34.6
streamlit>=1.35.0
pandas>=2.0.0
plotly>=5.18.0
requests>=2.32.0
anthropic>=0.40.0
numpy>=1.26.0
tabulate>=0.9.0
python-dotenv>=1.0.0
aiohttp>=3.10.0
web3>=7.0.0
pydantic>=2.0.0
websocket-client>=1.8.0
```

- [ ] **Step 2: Install the new dependency**

Run: `pip install plotly>=5.18.0`
Expected: Successfully installed plotly

- [ ] **Step 3: Verify import works**

Run: `python -c "import plotly.graph_objects as go; print(go.Figure())"`
Expected: Prints a Figure object without error

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "Add plotly dependency for dashboard charts"
```

---

## Task 2: Add `bankroll_history` to portfolio backend

**Files:**
- Modify: `config.py:154`
- Modify: `portfolio.py:111-123` (PortfolioState dataclass)
- Modify: `portfolio.py:372-380` (_init_fresh method)
- Modify: `portfolio.py:382-410` (save method)

- [ ] **Step 1: Add config setting**

In `config.py`, after line 154 (`PORTFOLIO_SAVE_INTERVAL`), add:

```python
BANKROLL_HISTORY_MAX_ENTRIES: int = int(os.getenv("BANKROLL_HISTORY_MAX_ENTRIES", "2000"))
```

- [ ] **Step 2: Add `bankroll_history` field to `PortfolioState`**

In `portfolio.py`, in the `PortfolioState` dataclass (line 111), add after the `pause_until` field (line 123):

```python
    bankroll_history: list = field(default_factory=list)  # [{timestamp, bankroll}]
```

- [ ] **Step 3: Update `_init_fresh` to include empty bankroll_history**

No change needed — `field(default_factory=list)` handles this. The dataclass default already creates an empty list on fresh init.

- [ ] **Step 4: Update `save()` to append bankroll snapshot**

In `portfolio.py`, in the `save` method, after `data = asdict(self.state)` (inside the `with self._lock:` block, line 388), add bankroll history append logic. Replace the save method's lock block:

```python
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
```

- [ ] **Step 5: Verify the change doesn't break loading**

Run: `python -c "from portfolio import Portfolio; p = Portfolio(5000); p._init_fresh(); p.save(); p.load(); print('bankroll_history:', len(p.state.bankroll_history), 'entries'); print('OK')"`
Expected: `bankroll_history: 1 entries` and `OK`

- [ ] **Step 6: Commit**

```bash
git add config.py portfolio.py
git commit -m "Add bankroll_history tracking for equity curve chart"
```

---

## Task 3: Dashboard rewrite — CSS theme + header + status banner

This is the first part of the dashboard rewrite. It replaces all CSS, the header bar, and the status banner. The file will not be fully functional until all dashboard tasks (3-7) are complete.

**Files:**
- Rewrite: `dashboard.py` (lines 1-396 replaced with new content)

- [ ] **Step 1: Write the new dashboard file — imports, config, CSS, helpers, header, status banner**

Rewrite `dashboard.py` with the following content. This is the foundation — tabs and tab content are added in subsequent tasks.

```python
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

# Tab content is added in subsequent tasks (Tasks 4-7)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown(
    f'<div class="footer">MeQ0L15 · POLYMARKET WAR MACHINE · AUTO-REFRESH {REFRESH_SECONDS}s · {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>',
    unsafe_allow_html=True,
)

time.sleep(REFRESH_SECONDS)
st.rerun()
```

- [ ] **Step 2: Verify the dashboard runs without errors**

Run: `streamlit run dashboard.py --server.headless true &` and check that no Python exceptions appear.
Kill the process after checking.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "Rewrite dashboard with cyberpunk CSS theme, header, and tab skeleton"
```

---

## Task 4: Overview tab content

**Files:**
- Modify: `dashboard.py` (add content inside `tab_overview`)

- [ ] **Step 1: Add Overview tab content**

Replace the `# Tab content is added in subsequent tasks (Tasks 4-7)` comment with the Overview tab content. Insert this code right after the `st.tabs()` call:

```python
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
            for trade in history[-20:]:  # last 20 trades
                closed_at = trade.get("closed_at", "")
                pnl_val = trade.get("pnl", 0) or 0
                if not closed_at:
                    continue
                try:
                    trade_time = pd.to_datetime(closed_at)
                except Exception:
                    continue
                # Find closest bankroll snapshot
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

        # Daily loss bar
        dl_ratio = min(daily_loss / limit_pct, 1.0) if limit_pct > 0 else 0.0
        dl_color = C_DANGER if daily_loss >= limit_pct * 0.8 else (C_WARNING if daily_loss >= limit_pct * 0.5 else C_PRIMARY)
        st.markdown(f'''
        <div style="display:flex;justify-content:space-between;font-size:0.6rem;color:{dl_color};margin-bottom:4px;">
          <span>DAILY LOSS</span><span>{daily_loss:.1f}% / {limit_pct:.0f}%</span>
        </div>
        <div style="height:4px;background:#00ff4115;border-radius:2px;overflow:hidden;margin-bottom:12px;">
          <div style="width:{dl_ratio*100:.0f}%;height:100%;background:{dl_color};box-shadow:0 0 6px {dl_color};border-radius:2px;"></div>
        </div>''', unsafe_allow_html=True)

        # Drawdown bar
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

        # Consecutive losses bar
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
```

- [ ] **Step 2: Verify Overview tab renders**

Run: `streamlit run dashboard.py --server.headless true &`
Open http://localhost:8501 in browser and verify Overview tab shows stat cards, equity chart placeholder (or chart if data exists), risk bars, and recent trades.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "Add Overview tab with stat cards, equity curve, risk bars, recent trades"
```

---

## Task 5: Positions tab content

**Files:**
- Modify: `dashboard.py` (add content inside `tab_positions`)

- [ ] **Step 1: Add Positions tab content**

Add the following code after the Overview tab block (before the footer):

```python
# ---------------------------------------------------------------------------
# TAB: POSITIONS
# ---------------------------------------------------------------------------

with tab_positions:
    # Open Positions
    st.markdown(f'<div class="section-hdr">// OPEN POSITIONS [{len(open_pos)}]</div>', unsafe_allow_html=True)

    if open_pos:
        header_html = '''
        <div style="display:grid;grid-template-columns:2.5fr 0.5fr 0.8fr 0.7fr 0.7fr 0.8fr 0.8fr 0.5fr;
                     background:#00ff4110;padding:8px 12px;border-bottom:1px solid #00ff4120;font-size:0.55rem;color:#00ff4160;letter-spacing:1px;">
          <div>MARKET</div><div>SIDE</div><div style="text-align:right">ENTRY</div>
          <div style="text-align:right">SHARES</div><div style="text-align:right">COST</div>
          <div style="text-align:right">MAX P&L</div><div style="text-align:right">ENDS</div><div></div>
        </div>'''

        rows_html = ""
        for pos in open_pos.values():
            side = pos.get("side", "")
            side_bg = "#00ff4115" if side == "YES" else "#ff004415"
            side_border = "#00ff4130" if side == "YES" else "#ff004430"
            side_color = "#00ff41" if side == "YES" else "#ff0044"
            max_pnl = pos.get("size", 0) - pos.get("cost_basis", 0)
            url = build_market_url(pos)
            end_txt = fmt_end_window(pos.get("end_date", ""))
            end_color = "#ff0044" if end_txt and ("h" in end_txt and not "d" in end_txt) else "#ffaa00"

            rows_html += f'''
            <div style="display:grid;grid-template-columns:2.5fr 0.5fr 0.8fr 0.7fr 0.7fr 0.8fr 0.8fr 0.5fr;
                         padding:8px 12px;border-bottom:1px solid #00ff4108;font-size:0.65rem;
                         transition:background 0.2s;"
                 onmouseover="this.style.background='#00ff4108'" onmouseout="this.style.background='transparent'">
              <div style="color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center;gap:4px;">
                <span style="color:#00ff41;font-size:0.4rem;">●</span> {pos.get("question", "")[:50]}
              </div>
              <div><span style="padding:1px 5px;background:{side_bg};color:{side_color};border:1px solid {side_border};font-size:0.55rem;">{side}</span></div>
              <div style="text-align:right;color:#ccc;">{pos.get("entry_price", 0):.3f}</div>
              <div style="text-align:right;color:#ccc;">{pos.get("size", 0):.2f}</div>
              <div style="text-align:right;color:#ccc;">${pos.get("cost_basis", 0):.2f}</div>
              <div style="text-align:right;color:#00ff41;text-shadow:0 0 6px #00ff4140;">{fmt_usd(max_pnl)}</div>
              <div style="text-align:right;color:{end_color};font-size:0.6rem;">{end_txt}</div>
              <div style="text-align:right;"><a href="{url}" target="_blank" style="color:#00ff4140;text-decoration:none;font-size:0.7rem;">↗</a></div>
            </div>'''

        st.markdown(f'<div style="border:1px solid #00ff4120;">{header_html}{rows_html}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:#00ff4140;font-size:0.65rem;padding:12px 0">// NO OPEN POSITIONS</div>', unsafe_allow_html=True)

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    # Trade History
    st.markdown(f'<div class="section-hdr">// TRADE HISTORY [{len(history)} CLOSED]</div>', unsafe_allow_html=True)

    if history:
        th_header = '''
        <div style="display:grid;grid-template-columns:0.7fr 2.5fr 0.5fr 0.7fr 0.7fr 0.8fr 0.8fr 0.5fr;
                     background:#00ff4110;padding:8px 12px;border-bottom:1px solid #00ff4120;font-size:0.55rem;color:#00ff4160;letter-spacing:1px;">
          <div>RESULT</div><div>MARKET</div><div>SIDE</div><div style="text-align:right">ENTRY</div>
          <div style="text-align:right">EXIT</div><div style="text-align:right">P&L</div>
          <div style="text-align:right">CLOSED</div><div></div>
        </div>'''

        th_rows = ""
        for pos in reversed(history):
            pnl_val = pos.get("pnl") or 0.0
            exit_price = pos.get("exit_price")
            if exit_price is not None and 0.01 < float(exit_price) < 0.99:
                result_badge = f'<span style="padding:1px 6px;background:#ffaa0015;color:#ffaa00;border:1px solid #ffaa0030;font-size:0.5rem;letter-spacing:1px;">@{float(exit_price):.2f}</span>'
            elif pnl_val > 0:
                result_badge = '<span style="padding:1px 6px;background:#00ff4115;color:#00ff41;border:1px solid #00ff4130;font-size:0.5rem;letter-spacing:1px;">WIN ▲</span>'
            else:
                result_badge = '<span style="padding:1px 6px;background:#ff004415;color:#ff0044;border:1px solid #ff004430;font-size:0.5rem;letter-spacing:1px;">LOSS ▼</span>'

            side = pos.get("side", "")
            side_color = "#00ff41" if side == "YES" else "#ff0044"
            pnl_c = "#00ff41" if pnl_val >= 0 else "#ff0044"
            closed = pos.get("closed_at", "")
            try:
                closed_dt = datetime.fromisoformat(closed).strftime("%m-%d %H:%M")
            except Exception:
                closed_dt = closed
            url = build_market_url(pos)

            th_rows += f'''
            <div style="display:grid;grid-template-columns:0.7fr 2.5fr 0.5fr 0.7fr 0.7fr 0.8fr 0.8fr 0.5fr;
                         padding:8px 12px;border-bottom:1px solid #00ff4108;font-size:0.65rem;"
                 onmouseover="this.style.background='#00ff4108'" onmouseout="this.style.background='transparent'">
              <div>{result_badge}</div>
              <div style="color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{pos.get("question", "")[:50]}</div>
              <div style="color:{side_color};">{side}</div>
              <div style="text-align:right;color:#888;">{pos.get("entry_price", 0):.3f}</div>
              <div style="text-align:right;color:#888;">{f"{float(exit_price):.3f}" if exit_price is not None else "—"}</div>
              <div style="text-align:right;color:{pnl_c};text-shadow:0 0 6px {pnl_c}40;">{fmt_usd(pnl_val)}</div>
              <div style="text-align:right;color:#00ff4150;font-size:0.6rem;">{closed_dt}</div>
              <div style="text-align:right;"><a href="{url}" target="_blank" style="color:#00ff4140;text-decoration:none;font-size:0.7rem;">↗</a></div>
            </div>'''

        st.markdown(f'<div style="border:1px solid #00ff4120;">{th_header}{th_rows}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:#00ff4140;font-size:0.65rem;padding:12px 0">// NO CLOSED TRADES YET</div>', unsafe_allow_html=True)

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    # P&L Distribution Chart
    st.markdown('<div class="section-hdr">// P&L DISTRIBUTION</div>', unsafe_allow_html=True)

    if history:
        pnl_values = [float(t.get("pnl", 0) or 0) for t in history]
        pnl_colors = [C_PRIMARY if v >= 0 else C_DANGER for v in pnl_values]
        trade_labels = [t.get("question", "")[:30] for t in history]

        fig_pnl = go.Figure()
        fig_pnl.add_trace(go.Bar(
            x=list(range(len(pnl_values))),
            y=pnl_values,
            marker=dict(
                color=pnl_colors,
                line=dict(width=0),
            ),
            hovertemplate=[
                f"{trade_labels[i]}<br>P&L: {fmt_usd(pnl_values[i])}<extra></extra>"
                for i in range(len(pnl_values))
            ],
        ))
        fig_pnl.update_layout(
            **plotly_theme(),
            height=200,
            showlegend=False,
            xaxis_title=None,
            yaxis_tickprefix="$",
            xaxis=dict(
                showticklabels=False,
                gridcolor="#00ff4110",
                zerolinecolor="#ffffff15",
            ),
            yaxis=dict(
                gridcolor="#00ff4110",
                zerolinecolor="#ffffff25",
                zerolinewidth=1,
                tickfont=dict(color="#00ff4140", size=9),
            ),
            bargap=0.15,
        )
        st.plotly_chart(fig_pnl, use_container_width=True, config={"displayModeBar": False})
    else:
        st.markdown('<div style="color:#00ff4140;font-size:0.65rem;padding:12px 0">// NO TRADE DATA</div>', unsafe_allow_html=True)
```

- [ ] **Step 2: Verify Positions tab renders**

Open http://localhost:8501, navigate to POSITIONS tab. Verify open positions table, trade history table, and P&L distribution chart render correctly.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "Add Positions tab with custom tables and P&L distribution chart"
```

---

## Task 6: Analytics tab content

**Files:**
- Modify: `dashboard.py` (add content inside `tab_analytics`)

- [ ] **Step 1: Add Analytics tab content**

Add the following code after the Positions tab block:

```python
# ---------------------------------------------------------------------------
# TAB: ANALYTICS
# ---------------------------------------------------------------------------

with tab_analytics:
    # Strategy Expectancy
    st.markdown('<div class="section-hdr">// STRATEGY EXPECTANCY</div>', unsafe_allow_html=True)

    by_strategy = strategy_report.get("by_strategy") or {}

    if by_strategy:
        # Donut chart + legend side by side
        chart_col, legend_col = st.columns([1, 1.5])

        strategy_names = list(by_strategy.keys())
        strategy_trades = [s.get("trades", 0) for s in by_strategy.values()]
        neon_colors = ["#00ff41", "#ffaa00", "#ff0044", "#00e5ff", "#ff00ff", "#ffffff"]
        chart_colors = neon_colors[:len(strategy_names)]

        with chart_col:
            fig_donut = go.Figure()
            fig_donut.add_trace(go.Pie(
                labels=strategy_names,
                values=strategy_trades,
                hole=0.6,
                marker=dict(colors=chart_colors, line=dict(color="#000", width=2)),
                textinfo="none",
                hovertemplate="%{label}<br>%{value} trades<br>%{percent}<extra></extra>",
            ))
            total_t = sum(strategy_trades)
            fig_donut.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="Courier New, monospace", color="#00ff4160", size=10),
                margin=dict(l=10, r=10, t=10, b=10),
                showlegend=False,
                height=200,
                annotations=[dict(
                    text=f"<b>{total_t}</b><br><span style='font-size:9px;color:#00ff4160'>TRADES</span>",
                    x=0.5, y=0.5, font_size=20, font_color="#ffffff",
                    showarrow=False,
                )],
            )
            st.plotly_chart(fig_donut, use_container_width=True, config={"displayModeBar": False})

        with legend_col:
            for i, (name, summary) in enumerate(by_strategy.items()):
                color = chart_colors[i] if i < len(chart_colors) else "#ccc"
                wr = summary.get("win_rate", 0.0)
                trades = summary.get("trades", 0)
                avg_pnl = summary.get("avg_pnl", 0.0)
                st.markdown(f'''
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:0.7rem;">
                  <div style="width:10px;height:10px;background:{color};box-shadow:0 0 6px {color};flex-shrink:0;"></div>
                  <span style="color:#ccc;flex:1;">{name}</span>
                  <span style="color:{color};">{wr:.0f}% · {trades} trades · avg {fmt_usd(avg_pnl)}</span>
                </div>''', unsafe_allow_html=True)

        # Strategy table
        st.markdown('<div style="margin-top:12px;"></div>', unsafe_allow_html=True)
        strat_header = '''
        <div style="display:grid;grid-template-columns:1.5fr repeat(7,1fr);
                     background:#00ff4110;padding:6px 10px;border-bottom:1px solid #00ff4120;font-size:0.5rem;color:#00ff4160;letter-spacing:1px;">
          <div>STRATEGY</div><div style="text-align:right">TRADES</div><div style="text-align:right">RESOLVED</div>
          <div style="text-align:right">WIN RATE</div><div style="text-align:right">AVG P&L</div>
          <div style="text-align:right">TOTAL P&L</div><div style="text-align:right">AVG EDGE</div>
          <div style="text-align:right">AVG AI</div>
        </div>'''
        strat_rows = ""
        for name, s in by_strategy.items():
            tp = s.get("total_pnl", 0.0)
            tp_color = "#00ff41" if tp >= 0 else "#ff0044"
            ai_conf = s.get("avg_ai_confidence", 0.0)
            ai_txt = f"{ai_conf:.2f}" if ai_conf else "—"
            strat_rows += f'''
            <div style="display:grid;grid-template-columns:1.5fr repeat(7,1fr);padding:6px 10px;
                         border-bottom:1px solid #00ff4108;font-size:0.6rem;"
                 onmouseover="this.style.background='#00ff4108'" onmouseout="this.style.background='transparent'">
              <div style="color:#ccc;">{name}</div>
              <div style="text-align:right;color:#ccc;">{s.get("trades", 0)}</div>
              <div style="text-align:right;color:#ccc;">{s.get("resolved_trades", 0)}</div>
              <div style="text-align:right;color:#ffaa00;">{s.get("win_rate", 0.0):.1f}%</div>
              <div style="text-align:right;color:#ccc;">{s.get("avg_pnl", 0.0):+.2f}</div>
              <div style="text-align:right;color:{tp_color};">{tp:+.2f}</div>
              <div style="text-align:right;color:#ccc;">{s.get("avg_edge_pct", 0.0):.1f}%</div>
              <div style="text-align:right;color:#ccc;">{ai_txt}</div>
            </div>'''
        st.markdown(f'<div style="border:1px solid #00ff4120;">{strat_header}{strat_rows}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:#00ff4140;font-size:0.65rem;padding:12px 0">// NO STRATEGY DATA YET</div>', unsafe_allow_html=True)

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    # Shadow Fill Report
    st.markdown('<div class="section-hdr">// SHADOW FILL REPORT</div>', unsafe_allow_html=True)

    by_shadow = shadow_report.get("by_strategy") or {}
    if by_shadow:
        sh_header = '''
        <div style="display:grid;grid-template-columns:1.5fr repeat(6,1fr);
                     background:#00ff4110;padding:6px 10px;border-bottom:1px solid #00ff4120;font-size:0.5rem;color:#00ff4160;letter-spacing:1px;">
          <div>STRATEGY</div><div style="text-align:right">SIGNALS</div><div style="text-align:right">RESOLVED</div>
          <div style="text-align:right">OPEN</div><div style="text-align:right">WIN RATE</div>
          <div style="text-align:right">EXP / $1</div><div style="text-align:right">AVG EDGE</div>
        </div>'''
        sh_rows = ""
        for name, s in by_shadow.items():
            sh_rows += f'''
            <div style="display:grid;grid-template-columns:1.5fr repeat(6,1fr);padding:6px 10px;
                         border-bottom:1px solid #00ff4108;font-size:0.6rem;"
                 onmouseover="this.style.background='#00ff4108'" onmouseout="this.style.background='transparent'">
              <div style="color:#ccc;">{name}</div>
              <div style="text-align:right;color:#ccc;">{s.get("signals", 0)}</div>
              <div style="text-align:right;color:#ccc;">{s.get("resolved_signals", 0)}</div>
              <div style="text-align:right;color:#ffaa00;">{s.get("open_signals", 0)}</div>
              <div style="text-align:right;color:#ccc;">{s.get("win_rate", 0.0):.1f}%</div>
              <div style="text-align:right;color:#ccc;">{s.get("avg_pnl_per_dollar", 0.0):+.3f}</div>
              <div style="text-align:right;color:#ccc;">{s.get("avg_first_edge_pct", 0.0):.1f}%</div>
            </div>'''
        st.markdown(f'<div style="border:1px solid #00ff4120;">{sh_header}{sh_rows}</div>', unsafe_allow_html=True)

        # Recent resolved shadows
        recent_shadow = shadow_report.get("recent_resolved") or []
        if recent_shadow:
            st.markdown('<div style="margin-top:12px;font-size:0.55rem;color:#00ff4140;letter-spacing:1px;">RECENT RESOLVED</div>', unsafe_allow_html=True)
            rs_rows = ""
            for item in recent_shadow:
                pnl_d = float(item.get("pnl_per_dollar", 0.0))
                pc = "#00ff41" if pnl_d >= 0 else "#ff0044"
                url = item.get("market_url", "")
                rs_rows += f'''
                <div style="display:flex;justify-content:space-between;padding:3px 0;font-size:0.6rem;border-bottom:1px solid #00ff4108;">
                  <span style="color:#888;">{item.get("strategy_type", "")}</span>
                  <span style="color:#ccc;flex:2;margin:0 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{str(item.get("question", ""))[:50]}</span>
                  <span style="color:{pc};">{pnl_d:+.3f}</span>
                  <a href="{url}" target="_blank" style="color:#00ff4140;text-decoration:none;margin-left:6px;">↗</a>
                </div>'''
            st.markdown(f'<div style="border:1px solid #00ff4120;padding:8px 10px;">{rs_rows}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:#00ff4140;font-size:0.65rem;padding:12px 0">// NO SHADOW DATA</div>', unsafe_allow_html=True)

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    # AI Usage + Realtime Feed
    ai_rt_left, ai_rt_right = st.columns(2)

    with ai_rt_left:
        st.markdown('<div class="section-hdr">// AI USAGE</div>', unsafe_allow_html=True)
        if ai_stats:
            ai_c1, ai_c2 = st.columns(2)
            with ai_c1:
                st.markdown(neon_stat_card(
                    "TOTAL CALLS",
                    f"{ai_stats.get('total_calls', 0):,}",
                    ai_stats.get("model", "—"),
                ), unsafe_allow_html=True)
            with ai_c2:
                cost = ai_stats.get("estimated_cost_usd", 0.0)
                cost_color = "c-red" if cost > 1 else "c-green"
                st.markdown(neon_stat_card(
                    "EST. COST",
                    f"${cost:.4f}",
                    f"in={ai_stats.get('total_input_tokens', 0):,} out={ai_stats.get('total_output_tokens', 0):,}",
                    cost_color,
                ), unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:#00ff4140;font-size:0.65rem;padding:12px 0">// NO AI DATA</div>', unsafe_allow_html=True)

    with ai_rt_right:
        st.markdown('<div class="section-hdr">// REALTIME FEED</div>', unsafe_allow_html=True)
        if feed_status:
            connected = bool(feed_status.get("connected"))
            status_label = "CONNECTED" if connected else "DISCONNECTED"
            status_c = "c-green" if connected else "c-red"
            rt_c1, rt_c2 = st.columns(2)
            with rt_c1:
                st.markdown(neon_stat_card(
                    "FEED STATUS",
                    status_label,
                    f"{int(feed_status.get('watched_assets', 0)):,} assets",
                    status_c,
                ), unsafe_allow_html=True)
            with rt_c2:
                last_age = feed_status.get("last_message_age_seconds")
                age_txt = f"{float(last_age):.1f}s ago" if last_age is not None else "n/a"
                st.markdown(neon_stat_card(
                    "WIRE TRAFFIC",
                    f"{int(feed_status.get('message_count', 0)):,}",
                    f"{age_txt} · {int(feed_status.get('reconnect_count', 0))} reconn",
                    "c-green",
                ), unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:#00ff4140;font-size:0.65rem;padding:12px 0">// FEED OFFLINE</div>', unsafe_allow_html=True)
```

- [ ] **Step 2: Verify Analytics tab renders**

Open http://localhost:8501, navigate to ANALYTICS tab. Verify donut chart, strategy table, shadow report, AI usage and realtime feed cards all render.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "Add Analytics tab with strategy donut, shadow report, AI usage, realtime feed"
```

---

## Task 7: Risk tab + Config tab + final cleanup

**Files:**
- Modify: `dashboard.py` (add content inside `tab_risk` and `tab_config`)

- [ ] **Step 1: Add Risk tab content**

Add the following code after the Analytics tab block:

```python
# ---------------------------------------------------------------------------
# TAB: RISK
# ---------------------------------------------------------------------------

with tab_risk:
    st.markdown('<div class="section-hdr">// RISK GAUGES</div>', unsafe_allow_html=True)

    g1, g2, g3 = st.columns(3)

    def make_gauge(value: float, max_val: float, title: str, suffix: str = "%") -> go.Figure:
        """Create a semi-circle gauge with cyberpunk neon colors."""
        ratio = min(value / max_val, 1.0) if max_val > 0 else 0.0

        if ratio < 0.5:
            bar_color = "#00ff41"
        elif ratio < 0.8:
            bar_color = "#ffaa00"
        else:
            bar_color = "#ff0044"

        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=value,
            number=dict(
                font=dict(size=28, color=bar_color, family="Courier New, monospace"),
                suffix=suffix,
            ),
            gauge=dict(
                axis=dict(
                    range=[0, max_val],
                    tickwidth=0,
                    tickcolor="rgba(0,0,0,0)",
                    tickfont=dict(size=8, color="#00ff4140"),
                ),
                bar=dict(color=bar_color, thickness=0.7),
                bgcolor="rgba(0,255,65,0.06)",
                borderwidth=0,
                steps=[
                    dict(range=[0, max_val * 0.5], color="rgba(0,255,65,0.04)"),
                    dict(range=[max_val * 0.5, max_val * 0.8], color="rgba(255,170,0,0.04)"),
                    dict(range=[max_val * 0.8, max_val], color="rgba(255,0,68,0.04)"),
                ],
                threshold=dict(
                    line=dict(color="#ffffff30", width=2),
                    thickness=0.8,
                    value=max_val * 0.8,
                ),
            ),
            title=dict(
                text=title,
                font=dict(size=10, color="#00ff4160", family="Courier New, monospace"),
            ),
        ))
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Courier New, monospace"),
            margin=dict(l=20, r=20, t=40, b=10),
            height=200,
        )
        return fig

    with g1:
        fig_dl = make_gauge(daily_loss, limit_pct, "DAILY LOSS")
        st.plotly_chart(fig_dl, use_container_width=True, config={"displayModeBar": False})
        dl_warn = daily_loss >= limit_pct * 0.8
        dl_status = f"✗ BLOCKED" if blocked_daily else ("⚠ APPROACHING LIMIT" if dl_warn else f"✓ {limit_pct - daily_loss:.1f}% HEADROOM")
        dl_sc = C_DANGER if dl_warn else C_PRIMARY
        st.markdown(f'<div style="text-align:center;font-size:0.6rem;color:{dl_sc};">{dl_status}</div>', unsafe_allow_html=True)

    with g2:
        dd_stop = config.DRAWDOWN_STOP_THRESHOLD * 100
        fig_dd = make_gauge(dd, dd_stop, "DRAWDOWN FROM PEAK")
        st.plotly_chart(fig_dd, use_container_width=True, config={"displayModeBar": False})
        dd_warn = dd >= config.DRAWDOWN_REDUCE_THRESHOLD * 100
        dd_status = "✗ STOPPED" if blocked_drawdown else ("⚠ BET SIZE HALVED" if dd_warn else "✓ NORMAL SIZING")
        dd_sc = C_DANGER if dd_warn else C_PRIMARY
        st.markdown(f'<div style="text-align:center;font-size:0.6rem;color:{dd_sc};">{dd_status}</div>', unsafe_allow_html=True)

    with g3:
        pause_at = config.CONSECUTIVE_LOSS_PAUSE
        fig_cl = make_gauge(cons_losses, pause_at, "CONSECUTIVE LOSSES", suffix="")
        st.plotly_chart(fig_cl, use_container_width=True, config={"displayModeBar": False})
        cl_warn = cons_losses >= pause_at * 0.7
        streak_txt = f"W{cons_wins}" if cons_wins > 0 else (f"L{cons_losses}" if cons_losses > 0 else "NONE")
        cl_sc = C_DANGER if cl_warn else C_PRIMARY
        st.markdown(f'<div style="text-align:center;font-size:0.6rem;color:{cl_sc};">STREAK: {streak_txt}</div>', unsafe_allow_html=True)

    st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    # Risk detail cards
    st.markdown('<div class="section-hdr">// RISK DETAILS</div>', unsafe_allow_html=True)
    rd1, rd2, rd3 = st.columns(3)

    with rd1:
        st.markdown(f'''
        <div style="border:1px solid #00ff4120;padding:12px;background:#00ff4108;">
          <div style="font-size:0.55rem;color:#00ff4160;letter-spacing:1px;margin-bottom:6px;">DAILY LOSS LIMIT</div>
          <div style="display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:4px;">
            <span style="color:#888;">Realized loss</span><span style="color:#ccc;">{daily_loss:.1f}%</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:4px;">
            <span style="color:#888;">Limit</span><span style="color:#ccc;">{limit_pct:.0f}%</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:0.65rem;">
            <span style="color:#888;">Headroom</span><span style="color:#00ff41;">{max(0, limit_pct - daily_loss):.1f}%</span>
          </div>
        </div>''', unsafe_allow_html=True)

    with rd2:
        dd_reduce = config.DRAWDOWN_REDUCE_THRESHOLD * 100
        st.markdown(f'''
        <div style="border:1px solid #00ff4120;padding:12px;background:#00ff4108;">
          <div style="font-size:0.55rem;color:#00ff4160;letter-spacing:1px;margin-bottom:6px;">DRAWDOWN CONTROL</div>
          <div style="display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:4px;">
            <span style="color:#888;">Current</span><span style="color:#ccc;">{dd:.1f}%</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:4px;">
            <span style="color:#888;">Halve at</span><span style="color:#ffaa00;">{dd_reduce:.0f}%</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:0.65rem;">
            <span style="color:#888;">Stop at</span><span style="color:#ff0044;">{dd_stop:.0f}%</span>
          </div>
        </div>''', unsafe_allow_html=True)

    with rd3:
        st.markdown(f'''
        <div style="border:1px solid #00ff4120;padding:12px;background:#00ff4108;">
          <div style="font-size:0.55rem;color:#00ff4160;letter-spacing:1px;margin-bottom:6px;">LOSS STREAK CONTROL</div>
          <div style="display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:4px;">
            <span style="color:#888;">Current streak</span><span style="color:#ccc;">{cons_losses}</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:0.65rem;margin-bottom:4px;">
            <span style="color:#888;">Halve at</span><span style="color:#ffaa00;">{config.CONSECUTIVE_LOSS_REDUCE}</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:0.65rem;">
            <span style="color:#888;">Pause at</span><span style="color:#ff0044;">{pause_at}</span>
          </div>
        </div>''', unsafe_allow_html=True)
```

- [ ] **Step 2: Add Config tab content**

Add the following code after the Risk tab block:

```python
# ---------------------------------------------------------------------------
# TAB: CONFIG
# ---------------------------------------------------------------------------

with tab_config:
    st.markdown('<div class="section-hdr">// CONFIGURATION</div>', unsafe_allow_html=True)

    def cfg_row(label: str, val: str) -> str:
        return (
            f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
            f'border-bottom:1px dotted #00ff4115;font-size:0.65rem;">'
            f'<span style="color:#00ff4160;">{label}</span>'
            f'<span style="color:#ccc;">{val}</span></div>'
        )

    cfg1, cfg2, cfg3 = st.columns(3)

    with cfg1:
        st.markdown(f'''
        <div style="border:1px solid #00ff4120;padding:14px;background:#00ff4108;">
          <div style="font-size:0.55rem;color:#00ff4160;letter-spacing:2px;margin-bottom:10px;">// TRADING</div>
          {cfg_row("Min Edge", f"{config.MIN_EDGE_PCT}%")}
          {cfg_row("AI Min Edge", f"{config.AI_MIN_EDGE_PCT}%")}
          {cfg_row("AI Confidence", f"{config.MIN_AI_CONFIDENCE:.0%}")}
          {cfg_row("Bet Size", f"{config.BET_SIZE_PCT}%")}
          {cfg_row("Max Bet", f"${config.MAX_BET_SIZE}")}
          {cfg_row("Max Exposure", f"{config.MAX_EXPOSURE_PCT}%")}
          {cfg_row("Min Price", f"{config.ODDS_COMPARISON_MIN_PRICE}")}
          {cfg_row("Min Bookmakers", str(config.MIN_BOOKMAKER_COUNT))}
        </div>''', unsafe_allow_html=True)

    with cfg2:
        st.markdown(f'''
        <div style="border:1px solid #00ff4120;padding:14px;background:#00ff4108;">
          <div style="font-size:0.55rem;color:#00ff4160;letter-spacing:2px;margin-bottom:10px;">// RISK</div>
          {cfg_row("Daily Loss Limit", f"{config.DAILY_LOSS_LIMIT_PCT:.0%}")}
          {cfg_row("Drawdown Reduce", f"{config.DRAWDOWN_REDUCE_THRESHOLD:.0%}")}
          {cfg_row("Drawdown Stop", f"{config.DRAWDOWN_STOP_THRESHOLD:.0%}")}
          {cfg_row("Consec. Loss Reduce", str(config.CONSECUTIVE_LOSS_REDUCE))}
          {cfg_row("Consec. Loss Pause", str(config.CONSECUTIVE_LOSS_PAUSE))}
          {cfg_row("Pause Duration", f"{config.PAUSE_DURATION_MINUTES}min")}
        </div>''', unsafe_allow_html=True)

    with cfg3:
        ai_model_short = config.AI_MODEL.split("-")[1] if "-" in config.AI_MODEL else config.AI_MODEL
        st.markdown(f'''
        <div style="border:1px solid #00ff4120;padding:14px;background:#00ff4108;">
          <div style="font-size:0.55rem;color:#00ff4160;letter-spacing:2px;margin-bottom:10px;">// OPERATIONAL</div>
          {cfg_row("Poll Interval", f"{config.POLL_INTERVAL}s")}
          {cfg_row("AI Model", ai_model_short)}
          {cfg_row("Paper Trading", str(config.PAPER_TRADING))}
          {cfg_row("Min Volume 24h", f"${config.MIN_VOLUME_24H}")}
          {cfg_row("Min Liquidity", f"${config.MIN_LIQUIDITY}")}
          {cfg_row("Realtime Feed", str(config.REALTIME_MARKET_WS_ENABLED))}
          {cfg_row("Realtime Gate", str(config.ENABLE_REALTIME_EXECUTION_GATE))}
          {cfg_row("Max Spread", f"{config.REALTIME_GATE_MAX_SPREAD:.2f}")}
          {cfg_row("Min Depth", f"${config.REALTIME_GATE_MIN_DEPTH_USD:.2f}")}
        </div>''', unsafe_allow_html=True)
```

- [ ] **Step 3: Verify Risk and Config tabs render**

Open http://localhost:8501, navigate to RISK tab — verify 3 gauge charts and 3 detail cards.
Navigate to CONFIG tab — verify 3-column terminal-style key-value display.

- [ ] **Step 4: Verify all 5 tabs work end-to-end**

Click through all tabs: OVERVIEW → POSITIONS → ANALYTICS → RISK → CONFIG. Verify no Python errors, all sections render, auto-refresh works (wait 10 seconds, page should refresh).

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "Add Risk tab with gauge charts and Config tab with terminal-style display"
```

---

## Task 8: Final integration verification

**Files:**
- No new changes — verification only

- [ ] **Step 1: Verify portfolio.py bankroll_history integration**

Run: `python -c "
from portfolio import Portfolio
p = Portfolio(5000)
p._init_fresh()
p.save()
p.load()
print('History length:', len(p.state.bankroll_history))
p.save()
p.load()
print('After 2nd save:', len(p.state.bankroll_history))
assert len(p.state.bankroll_history) == 2
print('bankroll_history OK')
"`
Expected: History length 1, after 2nd save 2, assertion passes.

- [ ] **Step 2: Verify dashboard imports and data loading**

Run: `python -c "
import dashboard
print('Dashboard module loaded OK')
"`
Note: This will fail because Streamlit modules need the Streamlit runtime. Instead verify with:
Run: `python -c "import plotly.graph_objects as go; import streamlit; import pandas; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Run streamlit and visually check all tabs**

Run: `streamlit run dashboard.py`
Open http://localhost:8501 and check:
- Header: logo, mode badge, status, network, timestamp
- Status banner: green pill "BOT ACTIVE"
- OVERVIEW: 4 stat cards, equity curve (or placeholder), risk bars, recent trades
- POSITIONS: open positions table, trade history table, P&L bar chart
- ANALYTICS: strategy donut chart + table, shadow report, AI usage, realtime feed
- RISK: 3 gauge charts, 3 detail cards
- CONFIG: 3-column key-value display
- Footer: timestamp, auto-refresh

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "Complete cyberpunk dashboard redesign with Plotly charts and tabbed navigation"
```
