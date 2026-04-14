# Opus Review History & Compliance Check

## Summary

Add persistent review history so each Opus trade review can evaluate whether previous recommendations were actually applied. Opus outputs a "PREVIOUS RECOMMENDATIONS STATUS" section before the new review, comparing prior recommendations against current config + post-review trade performance.

## Components

### 1. Review History Storage

- **File:** `data/btc/review_history.jsonl` (append-only)
- **Entry format per line:**
  ```json
  {
    "timestamp": "2026-04-15T10:30:00+00:00",
    "total_trades_at_review": 450,
    "win_rate": 45.8,
    "total_pnl": 3.22,
    "opus_analysis": "... full text ...",
    "config_snapshot": {...}
  }
  ```
- Written at end of every `run_review()` call (not just when Opus succeeds)

### 2. Config Snapshot Helper

`_snapshot_config()` in `btc/trade_journal.py` returns:
```python
{
    "blocked_strategies": list[str],
    "max_ai_confidence": float,
    "min_confidence": float,
    "bet_confidence_scale": float,
    "consecutive_loss_pause": int,
    "consecutive_loss_reduce": int,
    "pause_duration_min": int,
    "max_concurrent_windows": int,
    "max_entry_price": float,
}
```

### 3. Opus Prompt Update

`_opus_review()` now also accepts `trades_since_last_review` and `prev_review` context. Prompt gets new sections BEFORE the existing EVALUATE section:

```
PREVIOUS REVIEW ({prev_timestamp}, {prev_total_trades} trades):
{prev_opus_analysis}

CONFIG AT PREVIOUS REVIEW:
{prev_config_snapshot_json}

CURRENT CONFIG:
{current_config_snapshot_json}

TRADES SINCE PREVIOUS REVIEW: {n_new_trades}
POST-REVIEW STATS: {post_review_stats}

FIRST: Output a "# PREVIOUS RECOMMENDATIONS STATUS" section.
For each actionable recommendation from the previous review, mark:
- ✅ APPLIED — current config/behavior matches the recommendation
- ❌ NOT APPLIED — recommendation was made but config unchanged
- ⚠️ PARTIAL — some aspects applied, others not

Include evidence (specific config values or trade stats).

THEN proceed with normal evaluation of the full dataset.
```

When there is NO previous review (first run), skip the new sections and use the existing prompt.

### 4. Data Flow

```
run_review() called
  ↓
Load previous review from review_history.jsonl (last line)
  ↓
Load all trades from trade_ledger.jsonl
  ↓
Split trades: pre_last_review vs post_last_review (by timestamp)
  ↓
Compute stats (existing logic — on full dataset)
  ↓
Call _opus_review() with prev_review + post_review_stats
  ↓
Opus outputs "PREVIOUS RECOMMENDATIONS STATUS" + normal review
  ↓
Save strategy_review.json (existing behavior)
  ↓
Append new entry to review_history.jsonl
```

## Files Changed

| File | Change |
|---|---|
| `btc/trade_journal.py` | Add `_snapshot_config()`, `_load_last_review()`, `_append_history()`. Update `run_review()` to use history. Update `_opus_review()` prompt. |

## Edge Cases

- **First review ever** — `review_history.jsonl` doesn't exist → skip new prompt sections, normal flow
- **Corrupt history line** — skip bad lines, use last valid entry
- **History grows large** — `.jsonl` format is fine up to hundreds of MB; no rotation needed for now
- **Opus call fails** — history entry still written (with `opus_analysis=""` so we track review was attempted)
- **Config snapshot fails** — fallback to empty dict, review continues
