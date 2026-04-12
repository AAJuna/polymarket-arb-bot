# Two-Stage AI Pipeline: GPT-4o-mini Filter → Sonnet Deep Analysis

## Problem

The sports bot uses a single AI model (Haiku) for both filtering and deep analysis. Haiku is too conservative for financial judgment calls — it returns low confidence (38-52%) and SKIPs nearly all candidates. The match analytics model (Poisson, xG, form, H2H) computes good probabilities but the AI overrides them with its own lower confidence because it's not anchored to the model output.

## Solution

Replace the single Haiku gate with a two-stage pipeline:

1. **GPT-4o-mini** — quick filter: "Is this match worth analyzing?"
2. **Claude Sonnet** — deep analysis with full data: "Is there a real edge? YES or NO."

## Architecture

```
Math pre-filter (existing, unchanged)
        │
        ▼  ~5-10 candidates
┌─────────────────────────┐
│  Stage 1: GPT-4o-mini   │
│  Input: data summary    │
│  Output: PASS / REJECT  │
│  Cost: ~$0.001/call     │
└─────────────────────────┘
        │  ~2-3 PASS
        ▼
┌─────────────────────────┐
│  Stage 2: Claude Sonnet │
│  Input: full data       │
│  Output: YES/NO/SKIP    │
│  Cost: ~$0.014/call     │
└─────────────────────────┘
        │  1-2 trades
        ▼
     Executor
```

## Stage 1: GPT-4o-mini Filter

### Purpose

Quick, cheap pre-screening. Reject obviously bad candidates before spending Sonnet tokens.

### Input (data summary)

- Market question + end date
- Edge % and type (odds_comparison / same_market / cross_market)
- Polymarket price vs sportsbook consensus probability
- Bookmaker count and consensus odds
- Basic team stats summary (if available from match_analytics): recent form, win rate, avg goals

### Output

Structured JSON:
```json
{
  "verdict": "PASS" | "REJECT",
  "reason": "Brief one-sentence explanation"
}
```

### Behavior

- No deep reasoning required — just a sanity check
- REJECT if: edge looks like noise, team name mismatch suspected, market too illiquid, obvious data artifact
- PASS if: edge appears plausible given the data, worth deeper investigation
- Bias toward PASS — better to let Sonnet reject than to miss a good trade
- **Multi-sport support:** works for ALL sports (soccer, NBA, NFL, tennis, UFC, etc.), not just football. Adapt data evaluation to sport type — soccer has xG/form data from SportMonks, other sports rely on sportsbook odds consensus

### Model & Cost

- Model: `gpt-4o-mini` (configurable via `AI_FILTER_MODEL` env var)
- Pricing: $0.15/1M input, $0.60/1M output
- Expected tokens per call: ~800 input, ~50 output → ~$0.00015/call

## Stage 2: Claude Sonnet Deep Analysis

### Purpose

Thorough analysis using all available data sources. Makes the final trade decision.

### Input (full data)

Everything from Stage 1 PLUS:
- Full Poisson model output (home_win_prob, draw_prob, away_win_prob, expected goals)
- xG data and shots on target (weighted by recency)
- Recency-weighted team form (last 8 matches, decay=0.82)
- Head-to-head record
- Home/away advantage factor (12%)
- Lineup and absence info (when available)
- Attack and defense ratings
- ROI-style edge calculation
- GPT-4o-mini's filter verdict and reason (for context)

### Output

Existing `AIAnalysis` schema (unchanged):
```json
{
  "predicted_probability": 0.65,
  "confidence": 0.72,
  "reasoning": "...",
  "edge_detected": true,
  "recommended_side": "YES",
  "risk_factors": ["..."]
}
```

### Prompt Changes

- Explicitly instruct Sonnet to anchor confidence to the structured model output
- "The match model has computed probabilities using Poisson simulation with recency-weighted xG, form, and H2H data. Use these as your baseline anchor. Your confidence should reflect data quality and edge reliability, not general uncertainty about sports outcomes."
- Remove overly conservative language that caused Haiku to default-SKIP

### Model & Cost

- Model: `claude-sonnet-4-6` (configurable via `AI_DEEP_MODEL` env var)
- Pricing: $3.0/1M input, $15.0/1M output
- Expected tokens per call: ~2000 input, ~500 output → ~$0.014/call
- Upgradeable to Opus via env var change

## Implementation Changes

### New Dependencies

- `openai>=1.30.0` added to `requirements.txt`

### Config Changes (`config.py`)

New variables:
```python
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
AI_FILTER_MODEL: str = os.getenv("AI_FILTER_MODEL", "gpt-4o-mini")
AI_DEEP_MODEL: str = os.getenv("AI_DEEP_MODEL", "claude-sonnet-4-6")
```

Existing `AI_MODEL` becomes alias for `AI_DEEP_MODEL` (backward compat).

### AI Analyzer Changes (`ai_analyzer.py`)

- Add OpenAI client initialization alongside Anthropic client
- New method: `filter(opp) -> FilterResult` — calls GPT-4o-mini
- Existing `analyze(opp)` method: change model from `config.AI_MODEL` to `config.AI_DEEP_MODEL`
- Update prompt to anchor on model data
- Separate cost tracking: `_filter_calls`, `_filter_cost` vs `_deep_calls`, `_deep_cost`
- Separate stats files: `data/ai_stats_filter.json` and `data/ai_stats.json`

### Deduplication — No Double Processing

Critical: markets must NOT be filtered or analyzed more than once per window to avoid wasting API costs.

**Cache key:** `{condition_id}:{side}:{type}` (same as current `cache_key` format)

**Two-level dedup cache:**
- `_filter_cache` (TTLCache, TTL = market end_date or 30 min, whichever is shorter) — stores GPT-4o-mini filter results (PASS/REJECT). If a market was already filtered, return cached result immediately without calling GPT.
- `_deep_cache` (TTLCache, TTL = market end_date or 30 min) — stores Sonnet analysis results. If a market was already analyzed, return cached result immediately without calling Sonnet.
- `_filter_reject_cache` (TTLCache, TTL = 60 min) — markets rejected by filter get a longer cooldown so they don't keep getting re-filtered every cycle.

**Flow with dedup:**
```python
for opp in filtered:
    # Stage 1: check filter cache first
    filter_result = ai.filter(opp)          # returns cached if seen before
    if not filter_result or not filter_result.passed:
        continue                            # rejected or error, skip

    # Stage 2: check deep cache first
    analysis = ai.analyze(opp)              # returns cached if seen before
```

**Logging:** When cache hit, log `"filter cache hit"` or `"deep cache hit"` so dashboard/logs show dedup is working. No API call = no cost increment.

### Rejected Market Rotation

Markets rejected by GPT-4o-mini filter get cached in `_filter_reject_cache` (60 min TTL). On each cycle, the main loop skips rejected markets and moves on to the next candidates in the ranked list. This naturally rotates through all available sports markets — soccer, NBA, NFL, tennis, UFC, cricket, etc.

- If all soccer candidates are rejected, the bot automatically evaluates NBA, tennis, or other sports in the next slots
- `AI_SCAN_LIMIT` controls how many candidates per cycle get sent to the filter — rejected ones don't count against this limit, so more markets get evaluated
- Data availability adapts per sport: soccer gets full match analytics (xG, form, Poisson), other sports use sportsbook odds consensus as primary data source
- The Sonnet deep analysis prompt adapts its evaluation criteria based on available data — it uses match model when present, sportsbook odds when that's all there is

### Main Loop Changes (`main.py`)

Current flow (step 4):
```python
for opp in filtered:
    analysis = ai.analyze(opp)  # Haiku does everything
```

New flow:
```python
for opp in filtered:
    filter_result = ai.filter(opp)          # Stage 1: GPT-4o-mini (cached if seen)
    if filter_result and filter_result.passed:
        analysis = ai.analyze(opp)          # Stage 2: Sonnet deep (cached if seen)
    else:
        # log rejection, continue
```

### Dashboard Changes (`dashboard.py`)

Replace single "AI USAGE" section with split display:

```
// AI USAGE
GPT-4o-mini (filter)     Sonnet (deep)        Total
┌──────────┐             ┌──────────┐         ┌──────────┐
│ 48 calls │             │ 12 calls │         │ 60 calls │
│ $0.0082  │             │ $0.168   │         │ $0.176   │
└──────────┘             └──────────┘         └──────────┘
```

- Read from both `data/ai_stats_filter.json` and `data/ai_stats.json`
- Show per-model breakdown + combined total
- Include tokens breakdown (input/output) for each

### Environment Changes (`.env`)

New required variable:
```
OPENAI_API_KEY=sk-...
```

New optional variables:
```
AI_FILTER_MODEL=gpt-4o-mini
AI_DEEP_MODEL=claude-sonnet-4-6
```

## Cost Estimates

### Per cycle (~10 min interval)
| Stage | Calls | Cost |
|-------|-------|------|
| GPT-4o-mini filter | ~5 | ~$0.001 |
| Sonnet deep | ~2-3 | ~$0.028-0.042 |
| **Total** | ~7-8 | **~$0.03-0.04** |

### Per day (~144 cycles)
| Component | Cost |
|-----------|------|
| GPT-4o-mini | ~$0.15 |
| Sonnet | ~$4-6 |
| **Total** | **~$4-6/day** |

### Per month
| Component | Cost |
|-----------|------|
| GPT-4o-mini | ~$4.5 |
| Sonnet | ~$120-180 |
| **Total** | **~$125-185/month** |

## Migration Path

- `AI_DEEP_MODEL` env var makes it easy to switch Sonnet → Opus later
- Just change `AI_DEEP_MODEL=claude-opus-4-0-20250414` in `.env`
- Pricing auto-adjusts in config.py based on model string
