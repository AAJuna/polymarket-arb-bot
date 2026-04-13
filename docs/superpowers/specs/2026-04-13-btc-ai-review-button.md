# BTC AI Review Button — Dashboard Feature

## Summary

Add a floating "AI REVIEW" button to the BTC 5M dashboard tab that triggers a full trade review via Claude Opus in the background, then displays the analysis inline below the terminal dashboard. Include an ON/OFF toggle in the CONFIG tab.

## Components

### 1. Floating Button (BTC 5M tab, inside terminal HTML)

- Position: fixed bottom-right corner of terminal container
- Style: cyberpunk theme matching existing terminal (Orbitron font, green glow)
- States:
  - **IDLE**: green outline, clickable — label "AI REVIEW"
  - **RUNNING**: orange, spinning animation, disabled — label "ANALYZING..."
  - **DONE**: auto-transitions back to IDLE after results render
- Hidden when config toggle is OFF
- Click triggers `st.session_state["btc_review_requested"] = True`

### 2. Background Review (Streamlit Python side)

- On each rerun, check `st.session_state.get("btc_review_requested")`
- If True and not already running:
  - Set `st.session_state["btc_review_status"] = "running"`
  - Spawn `threading.Thread(target=_run_btc_review, daemon=True)`
  - Thread calls `btc.trade_journal.run_review()`
  - Thread stores result dict in `st.session_state["btc_review_result"]`
  - Thread sets `st.session_state["btc_review_status"] = "done"`
  - Reset `btc_review_requested = False`
- Auto-refresh (existing 10s cycle) picks up status changes

### 3. Inline Results Section (below terminal HTML)

- Renders when `st.session_state.get("btc_review_status") == "done"`
- Still inside `with tab_btc:`, below `components.html(...)`
- Uses `st.html(...)` with cyberpunk-styled card
- Content sections:
  - **Header**: "AI STRATEGY REVIEW" + timestamp
  - **Stats Grid**: total trades, win rate, P&L, avg win, avg loss (4-col grid)
  - **Strategy Breakdown**: table — strategy name, trades, win rate, P&L, ROI
  - **Confidence Brackets**: table — bracket, trades, win rate, P&L
  - **Opus Analysis**: full AI recommendation text, rendered as monospace block
- "DISMISS" button at bottom — sets `btc_review_status = None`

### 4. Config Tab Toggle

- Location: CONFIG tab, column 3 (OPERATIONAL panel)
- New row: "AI Review Button" — ON/OFF
- Uses `st.checkbox` or equivalent, stored in `st.session_state["btc_review_enabled"]`
- Default: ON (True)
- When OFF: floating button hidden in BTC 5M tab, review cannot be triggered

## Files Changed

| File | Change |
|---|---|
| `dashboard.py` | Floating button in terminal HTML, threading logic, inline results section, config toggle |

No changes to `btc/trade_journal.py` — `run_review()` already returns the complete dict needed.

## Data Flow

```
User clicks button
  → st.session_state["btc_review_requested"] = True
  → Streamlit rerun detects flag
  → Spawns daemon thread calling run_review()
  → Thread writes result to st.session_state["btc_review_result"]
  → Thread sets status to "done"
  → Next auto-refresh renders inline results
  → User clicks DISMISS → status reset to None
```

## Edge Cases

- **Double-click**: Button disabled while status is "running" — no-op
- **No trades**: `run_review()` returns empty dict → show "No closed trades to review" message
- **Opus API failure**: `run_review()` still returns stats without `opus_analysis` key → render stats, show "AI analysis unavailable" for Opus section
- **Dashboard restart**: session_state resets — button back to IDLE, no stale results
- **Config OFF mid-review**: results still render if already done, button just hidden for next trigger
