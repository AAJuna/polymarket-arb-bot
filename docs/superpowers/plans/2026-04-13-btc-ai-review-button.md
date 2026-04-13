# BTC AI Review Button — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a floating "AI REVIEW" button to the BTC 5M dashboard tab that triggers Claude Opus analysis in a background thread, displays results inline, with an ON/OFF toggle in Config tab.

**Architecture:** Single-file change to `dashboard.py`. Floating button inside terminal HTML communicates intent via Streamlit's `postMessage` API to trigger `st.session_state` flags. Background `threading.Thread` calls existing `btc.trade_journal.run_review()`. Results render as a cyberpunk-styled inline section below the terminal.

**Tech Stack:** Streamlit, threading, st.session_state, existing `btc.trade_journal.run_review()`

---

### Task 1: Add session_state initialization and threading logic

**Files:**
- Modify: `dashboard.py:7-18` (imports)
- Modify: `dashboard.py:467-468` (after data loading, add session_state init + thread spawn)

- [ ] **Step 1: Add `threading` import**

At `dashboard.py:8`, after `import time`, add:

```python
import threading
```

- [ ] **Step 2: Add `btc.trade_journal` import**

At `dashboard.py:18`, after `from utils import ...`, add:

```python
from btc.trade_journal import run_review as btc_run_review
```

- [ ] **Step 3: Add session_state initialization + background thread logic**

At `dashboard.py:468`, after `btc_signal = load_json(BTC_SIGNAL_FILE)`, insert:

```python
# ---------------------------------------------------------------------------
# BTC AI Review — session state & background thread
# ---------------------------------------------------------------------------

BTC_REVIEW_FILE = Path("data/btc/strategy_review.json")

for _key, _default in [
    ("btc_review_enabled", True),
    ("btc_review_requested", False),
    ("btc_review_status", None),      # None | "running" | "done"
    ("btc_review_result", None),
]:
    if _key not in st.session_state:
        st.session_state[_key] = _default


def _bg_review():
    """Run trade review in background thread, store result in session_state."""
    try:
        result = btc_run_review()
        st.session_state["btc_review_result"] = result
        st.session_state["btc_review_status"] = "done"
    except Exception as e:
        st.session_state["btc_review_result"] = {"error": str(e)}
        st.session_state["btc_review_status"] = "done"


if st.session_state["btc_review_requested"] and st.session_state["btc_review_status"] != "running":
    st.session_state["btc_review_requested"] = False
    st.session_state["btc_review_status"] = "running"
    st.session_state["btc_review_result"] = None
    t = threading.Thread(target=_bg_review, daemon=True)
    t.start()
```

- [ ] **Step 4: Verify no syntax errors**

Run: `python -c "import py_compile; py_compile.compile('dashboard.py', doraise=True)"`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "feat(btc-review): add session_state init and background thread logic"
```

---

### Task 2: Add floating button to terminal HTML

**Files:**
- Modify: `dashboard.py:1776-1782` (before `</div>` closing `.terminal`, add floating button HTML+CSS)

- [ ] **Step 1: Add floating button CSS**

In the terminal HTML `<style>` block (inside `_terminal_html`), before the closing `</style>` tag at line 1680, add:

```css
.review-fab{{position:absolute;bottom:42px;right:16px;z-index:100;cursor:pointer;
  background:transparent;border:1px solid var(--grn);border-radius:4px;
  padding:8px 16px;font-family:'Orbitron',sans-serif;font-size:8px;
  font-weight:700;letter-spacing:2px;text-transform:uppercase;
  color:var(--grn);transition:all 0.3s ease;display:flex;align-items:center;gap:6px}}
.review-fab:hover{{background:rgba(0,255,136,0.1);box-shadow:0 0 20px rgba(0,255,136,0.2)}}
.review-fab.running{{border-color:var(--org);color:var(--org);pointer-events:none;opacity:0.7}}
.review-fab.running .fab-dot{{background:var(--org);box-shadow:0 0 8px var(--org);animation:pulse 1s infinite}}
.fab-dot{{width:6px;height:6px;border-radius:50%;background:var(--grn);box-shadow:0 0 6px var(--grn)}}
```

- [ ] **Step 2: Add floating button HTML**

In the terminal HTML, just before the `<div class="bottom-bar">` (line 1777), add the floating button. The button visibility depends on `_review_enabled` and its state depends on `_review_status`:

First, before the `_terminal_html` f-string starts (around line 1600), compute the button variables:

```python
_review_enabled = st.session_state.get("btc_review_enabled", True)
_review_status = st.session_state.get("btc_review_status")
_fab_class = "review-fab running" if _review_status == "running" else "review-fab"
_fab_label = "ANALYZING..." if _review_status == "running" else "AI REVIEW"
_fab_display = "flex" if _review_enabled else "none"
```

Then in the HTML, before `<div class="bottom-bar">`:

```html
<div class="{_fab_class}" id="reviewFab" style="display:{_fab_display}" onclick="triggerReview()">
  <span class="fab-dot"></span>{_fab_label}
</div>
```

- [ ] **Step 3: Add JavaScript to communicate click back to Streamlit**

In the `<script>` block (after existing scripts, before `</script>`), add:

```javascript
function triggerReview(){{
  var fab=document.getElementById('reviewFab');
  if(fab.classList.contains('running'))return;
  fab.classList.add('running');
  fab.querySelector('.fab-dot').style.background='#ff8800';
  fab.childNodes[1].textContent='ANALYZING...';
  window.parent.postMessage({{type:'streamlit:setComponentValue',value:true}},'*');
}}
```

- [ ] **Step 4: Replace `components.html()` call with Streamlit component that captures the click**

At line 1822, replace:

```python
    components.html(_terminal_html, height=740, scrolling=True)
```

with:

```python
    _review_clicked = components.html(_terminal_html, height=740, scrolling=True)
    if _review_clicked and st.session_state.get("btc_review_enabled", True):
        st.session_state["btc_review_requested"] = True
        st.rerun()
```

- [ ] **Step 5: Verify no syntax errors**

Run: `python -c "import py_compile; py_compile.compile('dashboard.py', doraise=True)"`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add dashboard.py
git commit -m "feat(btc-review): add floating AI REVIEW button to terminal HTML"
```

---

### Task 3: Add inline results section below terminal

**Files:**
- Modify: `dashboard.py:1822-1826` (after `components.html(...)`, add results section)

- [ ] **Step 1: Add inline results rendering**

After the `components.html(...)` + click handling code (from Task 2), add the results section. This goes inside `with tab_btc:`, before the `# Footer` section:

```python
    # ── AI Review Results (inline) ──
    _rev_status = st.session_state.get("btc_review_status")
    _rev_result = st.session_state.get("btc_review_result")

    if _rev_status == "running":
        st.html(
            '<div style="border:1px solid #ff880030;border-radius:4px;padding:16px;'
            'background:rgba(255,136,0,0.03);font-family:\'JetBrains Mono\',monospace;'
            'margin-top:8px;text-align:center">'
            '<div style="color:#ff8800;font-size:11px;font-family:Orbitron,sans-serif;'
            'font-weight:700;letter-spacing:3px;animation:pulse 1.5s infinite">'
            'OPUS ANALYZING TRADES...</div>'
            '<div style="color:#3a3a5a;font-size:9px;margin-top:6px">'
            'This may take 10-30 seconds</div></div>'
        )

    elif _rev_status == "done" and _rev_result:
        _rev_err = _rev_result.get("error")
        if _rev_err:
            st.html(
                f'<div style="border:1px solid #ff336630;border-radius:4px;padding:16px;'
                f'background:rgba(255,51,102,0.03);font-family:\'JetBrains Mono\',monospace;'
                f'margin-top:8px">'
                f'<div style="color:#ff3366;font-size:10px">REVIEW ERROR: {html_esc(str(_rev_err))}</div>'
                f'</div>'
            )
        else:
            _rv_total = _rev_result.get("total_trades", 0)
            _rv_wr = _rev_result.get("win_rate", 0)
            _rv_pnl = _rev_result.get("total_pnl", 0)
            _rv_wins = _rev_result.get("wins", 0)
            _rv_losses = _rev_result.get("losses", 0)
            _rv_avg_win = _rev_result.get("avg_win", 0)
            _rv_avg_loss = _rev_result.get("avg_loss", 0)
            _rv_strats = _rev_result.get("strategies", {})
            _rv_brackets = _rev_result.get("confidence_brackets", {})
            _rv_opus = _rev_result.get("opus_analysis", "")
            _rv_time = _rev_result.get("reviewed_at", "")[:19].replace("T", " ")
            _rv_pnl_color = "#00ff88" if _rv_pnl >= 0 else "#ff3366"

            # Strategy rows
            _strat_rows = ""
            for _sname, _sd in _rv_strats.items():
                _s_pnl_c = "#00ff88" if _sd.get("pnl", 0) >= 0 else "#ff3366"
                _strat_rows += (
                    f'<div style="display:grid;grid-template-columns:1fr repeat(4,80px);'
                    f'gap:4px;padding:4px 0;border-bottom:1px solid #1a1a2e;font-size:9px">'
                    f'<div style="color:#00aaff">{html_esc(_sname)}</div>'
                    f'<div style="color:#e0e0e8;text-align:center">{_sd.get("trades",0)}</div>'
                    f'<div style="color:#e0e0e8;text-align:center">{_sd.get("win_rate",0):.1f}%</div>'
                    f'<div style="color:{_s_pnl_c};text-align:center">${_sd.get("pnl",0):+.2f}</div>'
                    f'<div style="color:#e0e0e8;text-align:center">{_sd.get("roi",0):+.1f}%</div></div>'
                )

            # Confidence bracket rows
            _bracket_rows = ""
            for _bname, _bd_item in _rv_brackets.items():
                _b_pnl_c = "#00ff88" if _bd_item.get("total_pnl", 0) >= 0 else "#ff3366"
                _bracket_rows += (
                    f'<div style="display:grid;grid-template-columns:1fr repeat(3,80px);'
                    f'gap:4px;padding:4px 0;border-bottom:1px solid #1a1a2e;font-size:9px">'
                    f'<div style="color:#00ffcc">{html_esc(_bname)}</div>'
                    f'<div style="color:#e0e0e8;text-align:center">{_bd_item.get("trades",0)}</div>'
                    f'<div style="color:#e0e0e8;text-align:center">{_bd_item.get("win_rate",0):.1f}%</div>'
                    f'<div style="color:{_b_pnl_c};text-align:center">${_bd_item.get("total_pnl",0):+.2f}</div>'
                    f'</div>'
                )

            # Opus text
            _opus_html = ""
            if _rv_opus:
                _opus_lines = html_esc(_rv_opus).replace("\n", "<br>")
                _opus_html = (
                    f'<div style="margin-top:12px;border-top:1px solid #1a1a2e;padding-top:12px">'
                    f'<div style="color:#ff8800;font-family:Orbitron,sans-serif;font-size:8px;'
                    f'font-weight:700;letter-spacing:2px;margin-bottom:8px">'
                    f'OPUS STRATEGIC ANALYSIS</div>'
                    f'<div style="color:#b0b0c0;font-size:9px;line-height:1.7;'
                    f'white-space:pre-wrap">{_opus_lines}</div></div>'
                )
            else:
                _opus_html = (
                    '<div style="margin-top:12px;border-top:1px solid #1a1a2e;padding-top:12px;'
                    'color:#3a3a5a;font-size:9px;text-align:center">'
                    'AI analysis unavailable (no API key or API error)</div>'
                )

            st.html(
                f'<div style="border:1px solid #00ff8820;border-radius:4px;padding:16px 20px;'
                f'background:rgba(0,255,136,0.02);font-family:\'JetBrains Mono\',monospace;'
                f'margin-top:8px">'
                # Header
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'margin-bottom:14px">'
                f'<div style="color:#00ff88;font-family:Orbitron,sans-serif;font-size:10px;'
                f'font-weight:700;letter-spacing:3px">AI STRATEGY REVIEW</div>'
                f'<div style="color:#3a3a5a;font-size:8px">{_rv_time} UTC</div></div>'
                # Stats grid
                f'<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;'
                f'margin-bottom:14px">'
                f'<div style="text-align:center;padding:8px;background:#0a0a12;border-radius:3px">'
                f'<div style="color:#3a3a5a;font-size:7px;letter-spacing:1px">TRADES</div>'
                f'<div style="color:#00aaff;font-size:16px;font-weight:700;'
                f'font-family:Orbitron,sans-serif">{_rv_total}</div></div>'
                f'<div style="text-align:center;padding:8px;background:#0a0a12;border-radius:3px">'
                f'<div style="color:#3a3a5a;font-size:7px;letter-spacing:1px">WIN RATE</div>'
                f'<div style="color:#00ff88;font-size:16px;font-weight:700;'
                f'font-family:Orbitron,sans-serif">{_rv_wr:.1f}%</div></div>'
                f'<div style="text-align:center;padding:8px;background:#0a0a12;border-radius:3px">'
                f'<div style="color:#3a3a5a;font-size:7px;letter-spacing:1px">P&L</div>'
                f'<div style="color:{_rv_pnl_color};font-size:16px;font-weight:700;'
                f'font-family:Orbitron,sans-serif">${_rv_pnl:+.2f}</div></div>'
                f'<div style="text-align:center;padding:8px;background:#0a0a12;border-radius:3px">'
                f'<div style="color:#3a3a5a;font-size:7px;letter-spacing:1px">WINS</div>'
                f'<div style="color:#00ff88;font-size:16px;font-weight:700;'
                f'font-family:Orbitron,sans-serif">{_rv_wins}</div></div>'
                f'<div style="text-align:center;padding:8px;background:#0a0a12;border-radius:3px">'
                f'<div style="color:#3a3a5a;font-size:7px;letter-spacing:1px">LOSSES</div>'
                f'<div style="color:#ff3366;font-size:16px;font-weight:700;'
                f'font-family:Orbitron,sans-serif">{_rv_losses}</div></div>'
                f'<div style="text-align:center;padding:8px;background:#0a0a12;border-radius:3px">'
                f'<div style="color:#3a3a5a;font-size:7px;letter-spacing:1px">AVG W/L</div>'
                f'<div style="font-size:10px;font-weight:700;font-family:Orbitron,sans-serif">'
                f'<span style="color:#00ff88">${_rv_avg_win:+.2f}</span>'
                f'<span style="color:#3a3a5a"> / </span>'
                f'<span style="color:#ff3366">${_rv_avg_loss:+.2f}</span></div></div>'
                f'</div>'
                # Strategy breakdown
                f'<div style="margin-bottom:12px">'
                f'<div style="color:#ff8800;font-family:Orbitron,sans-serif;font-size:8px;'
                f'font-weight:700;letter-spacing:2px;margin-bottom:6px">STRATEGY BREAKDOWN</div>'
                f'<div style="display:grid;grid-template-columns:1fr repeat(4,80px);gap:4px;'
                f'padding:4px 0;border-bottom:1px solid #1a1a2e40;font-size:8px;color:#3a3a5a">'
                f'<div>STRATEGY</div><div style="text-align:center">TRADES</div>'
                f'<div style="text-align:center">WIN %</div><div style="text-align:center">P&L</div>'
                f'<div style="text-align:center">ROI</div></div>'
                f'{_strat_rows}</div>'
                # Confidence brackets
                f'<div style="margin-bottom:4px">'
                f'<div style="color:#00ffcc;font-family:Orbitron,sans-serif;font-size:8px;'
                f'font-weight:700;letter-spacing:2px;margin-bottom:6px">CONFIDENCE BRACKETS</div>'
                f'<div style="display:grid;grid-template-columns:1fr repeat(3,80px);gap:4px;'
                f'padding:4px 0;border-bottom:1px solid #1a1a2e40;font-size:8px;color:#3a3a5a">'
                f'<div>BRACKET</div><div style="text-align:center">TRADES</div>'
                f'<div style="text-align:center">WIN %</div><div style="text-align:center">P&L</div>'
                f'</div>'
                f'{_bracket_rows}</div>'
                # Opus analysis
                f'{_opus_html}'
                f'</div>'
            )

        # Dismiss button
        if st.button("DISMISS REVIEW", key="btc_dismiss_review"):
            st.session_state["btc_review_status"] = None
            st.session_state["btc_review_result"] = None
            st.rerun()
```

- [ ] **Step 2: Verify no syntax errors**

Run: `python -c "import py_compile; py_compile.compile('dashboard.py', doraise=True)"`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat(btc-review): add inline results section with stats, strategy, and Opus analysis"
```

---

### Task 4: Add ON/OFF toggle in Config tab

**Files:**
- Modify: `dashboard.py:1490-1507` (Config tab, column 3 — OPERATIONAL panel)

- [ ] **Step 1: Add toggle row to OPERATIONAL panel**

At line 1505 (after the `cfg_row("Min Depth ($)", ...)` line, before `+ '</div>'`), add:

```python
            + cfg_row("AI Review Btn", "ON" if st.session_state.get("btc_review_enabled", True) else "OFF")
```

- [ ] **Step 2: Add Streamlit checkbox below the config panels**

After the three config columns close (after line 1507's closing `)`), but still inside `with tab_config:`, add:

```python
    # BTC AI Review toggle
    _review_on = st.checkbox(
        "Enable BTC AI Review Button",
        value=st.session_state.get("btc_review_enabled", True),
        key="btc_review_toggle",
    )
    if _review_on != st.session_state.get("btc_review_enabled", True):
        st.session_state["btc_review_enabled"] = _review_on
        st.rerun()
```

- [ ] **Step 3: Verify no syntax errors**

Run: `python -c "import py_compile; py_compile.compile('dashboard.py', doraise=True)"`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat(btc-review): add ON/OFF toggle in Config tab"
```

---

### Task 5: Integration test — verify full flow

**Files:**
- No new files

- [ ] **Step 1: Run Streamlit locally**

Run: `streamlit run dashboard.py`

- [ ] **Step 2: Verify BTC 5M tab shows floating button**

Navigate to BTC 5M tab. Confirm:
- Green "AI REVIEW" floating button visible at bottom-right of terminal
- Button has cyberpunk styling matching terminal theme

- [ ] **Step 3: Test review trigger**

Click "AI REVIEW" button. Confirm:
- Button changes to orange "ANALYZING..." state
- Inline "OPUS ANALYZING TRADES..." loading indicator appears below terminal
- After review completes, inline results section renders with stats, strategy breakdown, confidence brackets, and Opus analysis

- [ ] **Step 4: Test dismiss**

Click "DISMISS REVIEW" button. Confirm results section disappears.

- [ ] **Step 5: Test config toggle**

Go to CONFIG tab. Uncheck "Enable BTC AI Review Button". Go back to BTC 5M tab. Confirm floating button is hidden.

- [ ] **Step 6: Final commit**

```bash
git add dashboard.py
git commit -m "feat(btc-review): integration verified — AI review button complete"
```
