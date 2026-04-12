# Dashboard Cyberpunk Redesign — Design Spec

**Date:** 2026-04-12
**Scope:** Total overhaul of Streamlit dashboard (`dashboard.py`) — visual, layout, and data visualization
**Approach:** Streamlit Native + Plotly (Approach A)

---

## 1. Overview

Replace the current dark crypto theme dashboard with a full cyberpunk/hacker aesthetic. The redesign covers three axes:

1. **Visual**: Neon glow, scanlines, monospace typography, terminal-inspired aesthetic
2. **Layout**: Tabbed navigation replacing single-page scroll
3. **Data Visualization**: Plotly charts (equity curve, P&L bars, strategy donut, risk gauges)

The dashboard remains a single-file Streamlit app (`dashboard.py`) reading from existing JSON data files. One new data field (`bankroll_history`) is added to `portfolio.json` to power the equity curve chart.

---

## 2. Color Palette

| Token         | Hex        | Usage                              |
|---------------|------------|-------------------------------------|
| `BG`          | `#000000`  | Page background                     |
| `BG_CARD`     | `#00ff4108`| Card/panel background (green 8%)    |
| `BG_CARD_RED` | `#ff004408`| Card background for loss context    |
| `BG_CARD_AMB` | `#ffaa0008`| Card background for warning context |
| `PRIMARY`     | `#00ff41`  | Profit, active, primary accent      |
| `DANGER`      | `#ff0044`  | Loss, danger, error                 |
| `WARNING`     | `#ffaa00`  | Caution, warning, amber             |
| `TEXT`         | `#cccccc`  | Body text                           |
| `TEXT_DIM`     | `#00ff4160`| Labels, secondary info              |
| `TEXT_MUTED`   | `#00ff4140`| Timestamps, footnotes               |
| `BORDER`       | `#00ff4120`| Card/table borders                  |
| `BORDER_HOVER` | `#00ff4140`| Hover state borders                 |
| `GLOW_GREEN`   | `0 0 10px #00ff4140` | text-shadow for green values |
| `GLOW_RED`     | `0 0 10px #ff004440` | text-shadow for red values   |

---

## 3. Typography

- **Font**: System monospace stack (`'Courier New', 'Consolas', 'Monaco', monospace`)
- **Labels**: 8-9px, `text-transform: uppercase`, `letter-spacing: 1-2px`, color `TEXT_DIM`
- **Section headers**: `// SECTION NAME` comment-style prefix
- **Stat values**: 14-22px, `font-weight: bold`, full neon color + glow text-shadow
- **Body text**: 10-11px, color `TEXT`
- **Tab labels**: `[ TAB NAME ]` bracket style, 11px

---

## 4. Visual Effects

### Scanlines
CSS `repeating-linear-gradient` overlay on the entire page, barely visible:
```css
background: repeating-linear-gradient(
  0deg, transparent, transparent 2px,
  #00ff4103 2px, #00ff4103 4px
);
```
Applied via a fixed-position pseudo-element with `pointer-events: none`.

### Neon Glow
- **Text**: `text-shadow: 0 0 10px <color>40` on key metric values
- **Borders**: `box-shadow: 0 0 6px <color>` on focused/active elements
- **Charts**: Plotly SVG filter `drop-shadow` for line glow

### Hover Effects
- Table rows: `background` transitions to `#00ff4108` on hover
- Cards: border color shifts from `BORDER` to `BORDER_HOVER`

---

## 5. Layout — Tabbed Navigation

### Header Bar
Fixed top bar with:
- **Left**: Logo icon (⚔️ in bordered square) + "MeQ0L15" title (neon green, letter-spacing 2px) + "POLYMARKET WAR MACHINE" subtitle (dim) + mode badge (PAPER=amber border, LIVE=green border)
- **Right**: Status indicator (● ONLINE/OFFLINE), Network (POLYGON), Last Updated timestamp

### Status Banner
Full-width pill below header (same as current but restyled):
- Green pill: `BOT ACTIVE — SCANNING MARKETS`
- Yellow pill: `PAUSED — consecutive losses: N`
- Red pill: `BLOCKED — daily loss limit X% / Y%`

### Tab Bar
5 tabs using `st.tabs()`:
```
[ OVERVIEW ]  [ POSITIONS ]  [ ANALYTICS ]  [ RISK ]  [ CONFIG ]
```
Styled with CSS to match bracket/terminal aesthetic.

### Footer
Minimal: `MeQ0L15 · POLYMARKET WAR MACHINE · auto-refresh 10s · timestamp`

---

## 6. Tab Contents

### 6.1 OVERVIEW Tab

**Hero Stat Cards** — 4 columns:
| Card | Value Source | Color |
|------|-------------|-------|
| Total Equity | `current_bankroll + open_cost` | PRIMARY |
| Today P&L | `realized_bankroll - day_start` | PRIMARY/DANGER (dynamic) |
| Win Rate | `winning / total_trades * 100` | WARNING |
| Open Exposure | `sum(open cost_basis)` | TEXT white |

Each card: neon border, `BG_CARD` background, label (8px dim) + value (22px bold glow) + subtitle (9px dim).

**Equity Curve Chart** — Plotly line chart:
- Data source: `bankroll_history` from portfolio.json (list of `{timestamp, bankroll}` objects)
- Neon green line (#00ff41) with area fill gradient (25% → 0% opacity)
- SVG glow filter on the line
- Grid lines at #00ff4110
- Trade markers: diamond scatter points on the curve (green=win, red=loss) from `trade_history`
- Time range buttons rendered as Streamlit radio/selectbox styled as terminal buttons
- Plotly config: `displayModeBar=False`, transparent `paper_bgcolor` and `plot_bgcolor`

**Risk Mini-Panel** — Left column (50% width):
- 3 compact horizontal neon progress bars (daily loss, drawdown, loss streak)
- Label + value on same line, bar below
- Color dynamic: green when safe, yellow approaching limit, red at/above limit

**Recent Trades Mini-Table** — Right column (50% width):
- Last 5 closed trades from `trade_history`
- Columns: Market (truncated), Side, P&L
- Minimal styling, monospace

### 6.2 POSITIONS Tab

**Open Positions Table** — Custom HTML table (not `st.dataframe`):
- Columns: Market, Side (YES/NO badge), Action, Entry, Shares, Cost, Max P&L, Ends, Link (↗)
- Side badges: YES = green border pill, NO = red border pill
- Max P&L: green with glow
- Ends: amber countdown text
- Row hover: background glow
- Empty state: "No open positions" in dim text

**Trade History Table** — Custom HTML table:
- Columns: Result (badge), Market, Side, Entry, Exit, P&L, Closed, Link
- Result badges: `WIN ▲` (green), `LOSS ▼` (red), `@0.XX` (amber for partial settlement)
- P&L: colored + glow
- Sorted reverse chronological (newest first)

**P&L Distribution Chart** — Plotly bar chart:
- One bar per closed trade, chronological order
- Green bars above zero line (wins), red bars below (losses)
- Hover shows: market name, entry/exit price, P&L amount
- Zero line: dashed white at 15% opacity
- Neon glow on bars via marker line width

### 6.3 ANALYTICS Tab

**Strategy Expectancy** — Two-part display:
1. **Donut chart** (Plotly pie with hole=0.6): shows trade distribution by strategy
   - Each strategy gets a distinct neon color (green, amber, red, cyan)
   - Center text: total trade count
   - Legend with win rate and trade count per strategy
2. **Table** below donut: Strategy, Trades, Resolved, Win Rate, Avg P&L, Total P&L, Avg Edge, Avg AI Confidence

**Shadow Fill Report**:
- Strategy summary table (same style as current but cyberpunk-themed)
- Recent resolved shadow signals table with market links

**AI Usage** — 4 stat cards:
- Total Calls, Input Tokens, Output Tokens, Est. Cost
- Same card style as Overview hero cards but smaller

**Realtime Feed** — 4 stat cards:
- Feed Status (green/red indicator), Watched Assets, Quote Cache, Wire Traffic
- Connected/Disconnected status with neon dot

### 6.4 RISK Tab

**Risk Gauges** — 3 semi-circle arc gauges (Plotly indicator type "gauge"):
- Daily Loss: arc fills proportional to `daily_loss / limit_pct`
- Drawdown from Peak: arc fills proportional to `dd / dd_stop`
- Consecutive Losses: arc fills proportional to `cons_losses / pause_at`
- Color zones on arc: green (0-50%), yellow (50-80%), red (80-100%)
- Neon glow on the filled arc portion
- Center value: percentage or count in bold neon
- Label below each gauge

**Risk Detail Cards** — Below gauges, 3 cards with:
- Current value vs limit
- Status text (✓ headroom / ⚠ approaching / ✗ blocked)
- Additional context (e.g., "Bet size halved" for drawdown reduce zone)

### 6.5 CONFIG Tab

**Terminal-style key-value display** — 3 columns:
- **TRADING**: Min Edge, AI Confidence, Bet Size, Max Bet, Max Exposure
- **RISK**: Daily Loss Limit, Drawdown Reduce, Drawdown Stop, Consec. Pause At, Pause Duration
- **OPERATIONAL**: Poll Interval, AI Model, Paper Trading, Min Volume 24h, Min Liquidity, Realtime Feed, Realtime Gate, Max Spread, Min Depth

Each entry: monospace, dim label left, bright value right, separated by dotted line. Grouped in bordered panels with `// TRADING`, `// RISK`, `// OPERATIONAL` headers.

---

## 7. Data Requirements

### Existing Data (No Changes)
- `data/portfolio.json` — bankroll, positions, trade history, streaks
- `data/ai_stats.json` — AI call counts, tokens, cost
- `data/shadow_report.json` — shadow signal stats
- `data/strategy_expectancy.json` — per-strategy metrics
- `data/realtime_feed_status.json` — WebSocket feed health

### New Data Field
**`bankroll_history`** in `portfolio.json`:
```json
{
  "bankroll_history": [
    {"timestamp": "2026-04-05T10:00:00Z", "bankroll": 5000.00},
    {"timestamp": "2026-04-05T10:07:00Z", "bankroll": 5002.30},
    ...
  ]
}
```
- Appended by `portfolio.py` each cycle (or on bankroll change) during the main loop
- Used by equity curve chart to plot bankroll over time
- Rolling window: capped at last 2000 entries (oldest trimmed on each append) to prevent unbounded growth

---

## 8. Dependencies

### Existing (No Changes)
- `streamlit>=1.35.0`
- `pandas>=2.0.0`

### New
- `plotly>=5.18.0` — all charts (equity curve, P&L bars, donut, gauges)

Add `plotly` to `requirements.txt`.

---

## 9. File Changes

| File | Change |
|------|--------|
| `dashboard.py` | Complete rewrite — new CSS, tabbed layout, Plotly charts, custom HTML tables |
| `portfolio.py` | Add `bankroll_history` append logic in save method |
| `requirements.txt` | Add `plotly>=5.18.0` |

---

## 10. Streamlit-Specific Implementation Notes

- **Tabs**: Use `st.tabs(["OVERVIEW", "POSITIONS", "ANALYTICS", "RISK", "CONFIG"])` — CSS overrides to style as `[ TAB ]` bracket format
- **Custom HTML tables**: Render via `st.markdown(html, unsafe_allow_html=True)` — replaces all `st.dataframe` calls
- **Plotly charts**: Render via `st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})`
- **Scanline overlay**: Injected as CSS pseudo-element on `[data-testid="stAppViewContainer"]::after`
- **Auto-refresh**: Keep existing `time.sleep(REFRESH_SECONDS); st.rerun()` pattern
- **Equity curve time range**: Use `st.radio` with `horizontal=True`, styled via CSS to look like terminal buttons

---

## 11. Out of Scope

- No backend changes beyond `bankroll_history` append
- No WebSocket/real-time push (keep polling via `st.rerun`)
- No authentication or multi-user support
- No mobile-responsive design (desktop-first)
- No dark/light mode toggle (cyberpunk dark only)
