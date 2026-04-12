# Two-Stage AI Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-model Haiku AI gate with GPT-4o-mini filter → Claude Sonnet deep analysis pipeline, with per-model cost tracking in dashboard.

**Architecture:** OpenAI GPT-4o-mini screens candidates cheaply (~$0.001/call). Survivors go to Claude Sonnet for full analysis with match model data anchoring. Both stages use dedup caches to prevent re-processing. Dashboard shows split cost breakdown.

**Tech Stack:** OpenAI Python SDK, Anthropic Python SDK (existing), Streamlit dashboard (existing)

**Spec:** `docs/superpowers/specs/2026-04-13-two-stage-ai-pipeline.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `config.py` | Modify | Add OpenAI config vars, filter model pricing, deep model config |
| `ai_analyzer.py` | Modify | Add OpenAI filter stage, split stats tracking, update Sonnet prompt |
| `main.py` | Modify | Wire 2-stage flow: filter → deep, update startup banner + connectivity test |
| `dashboard.py` | Modify | Split AI usage display into filter + deep + total |
| `requirements.txt` | Modify | Add `openai>=1.30.0` |

---

### Task 1: Add OpenAI dependency and config

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py:31-60`

- [ ] **Step 1: Add openai to requirements.txt**

Add `openai>=1.30.0` after the `anthropic` line in `requirements.txt`:

```
anthropic>=0.40.0
openai>=1.30.0
```

- [ ] **Step 2: Add OpenAI and two-stage config vars to config.py**

After the existing Anthropic section (line 43), add OpenAI config and rename deep model config. The full replacement for the Anthropic Claude section (lines 32-60):

```python
# ---------------------------------------------------------------------------
# Anthropic Claude (deep analysis)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL: str = os.getenv("AI_MODEL", "claude-sonnet-4-6")
AI_DEEP_MODEL: str = os.getenv("AI_DEEP_MODEL", "") or AI_MODEL
AI_MAX_TOKENS: int = 512
AI_CALLS_PER_MINUTE: int = 30
AI_CACHE_TTL: int = 180  # seconds
AI_SKIP_CACHE_TTL: int = int(os.getenv("AI_SKIP_CACHE_TTL", "600"))
AI_MAX_CANDIDATES: int = int(os.getenv("AI_MAX_CANDIDATES", "2"))
AI_SCAN_LIMIT: int = int(os.getenv("AI_SCAN_LIMIT", "5"))
AI_MIN_EDGE_PCT: float = float(os.getenv("AI_MIN_EDGE_PCT", "6.0"))
AI_PAPER_MODE: str = os.getenv("AI_PAPER_MODE", "gate").strip().lower()  # gate | advisory

# ---------------------------------------------------------------------------
# OpenAI (filter stage)
# ---------------------------------------------------------------------------
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
AI_FILTER_MODEL: str = os.getenv("AI_FILTER_MODEL", "gpt-4o-mini")
AI_FILTER_CACHE_TTL: int = int(os.getenv("AI_FILTER_CACHE_TTL", "1800"))  # 30 min
AI_FILTER_REJECT_CACHE_TTL: int = int(os.getenv("AI_FILTER_REJECT_CACHE_TTL", "3600"))  # 60 min


def _ai_pricing_per_mtok(model: str) -> tuple[float, float]:
    """Return approximate input/output USD per 1M tokens for dashboard cost tracking."""
    model = model.lower()
    if "gpt-4o-mini" in model:
        return 0.15, 0.60
    if "gpt-4.1-nano" in model:
        return 0.10, 0.40
    if "gpt-4o" in model:
        return 2.50, 10.0
    if "haiku-4-5" in model:
        return 1.0, 5.0
    if "sonnet" in model:
        return 3.0, 15.0
    if "opus" in model:
        return 15.0, 75.0
    if "haiku" in model:
        return 0.25, 1.25
    return 3.0, 15.0


AI_INPUT_PRICE_PER_MTOK, AI_OUTPUT_PRICE_PER_MTOK = _ai_pricing_per_mtok(AI_DEEP_MODEL)
AI_FILTER_INPUT_PRICE_PER_MTOK, AI_FILTER_OUTPUT_PRICE_PER_MTOK = _ai_pricing_per_mtok(AI_FILTER_MODEL)
```

- [ ] **Step 3: Add OpenAI validation to validate() function**

In `config.py` `validate()`, add after the ANTHROPIC_API_KEY check (line 221):

```python
    if not OPENAI_API_KEY:
        issues.append(("warning", "OPENAI_API_KEY not set — GPT filter stage will be skipped, all candidates go directly to deep analysis"))
```

- [ ] **Step 4: Install dependency**

Run: `pip install openai>=1.30.0`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config.py
git commit -m "feat: add OpenAI config and dependency for 2-stage AI pipeline"
```

---

### Task 2: Add GPT-4o-mini filter to ai_analyzer.py

**Files:**
- Modify: `ai_analyzer.py:1-27` (imports and constants)
- Modify: `ai_analyzer.py:95-145` (AIAnalyzer class init and stats)

- [ ] **Step 1: Add OpenAI import and filter stats file**

At top of `ai_analyzer.py`, add OpenAI import after the anthropic import (line 14), and add filter stats file constant after `AI_STATS_FILE` (line 21):

```python
import anthropic
import openai

import config
from logger_setup import get_logger
from match_analytics import get_matchup_analysis_for_opportunity
from utils import RateLimiter, TTLCache, utcnow

AI_STATS_FILE = Path("data/ai_stats.json")
AI_FILTER_STATS_FILE = Path("data/ai_stats_filter.json")
```

- [ ] **Step 2: Add FilterResult dataclass**

After the `AIAnalysis` class (after line 92), add:

```python
@dataclass
class FilterResult:
    passed: bool
    reason: str
```

- [ ] **Step 3: Add filter caches, OpenAI client, and filter stats to AIAnalyzer.__init__**

Replace `AIAnalyzer.__init__` (lines 96-105):

```python
class AIAnalyzer:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self._openai_client = (
            openai.OpenAI(api_key=config.OPENAI_API_KEY)
            if config.OPENAI_API_KEY else None
        )
        self._rate_limiter = RateLimiter(calls_per_minute=config.AI_CALLS_PER_MINUTE)
        self._cache = TTLCache(ttl_seconds=config.AI_CACHE_TTL)
        self._skip_cache = TTLCache(ttl_seconds=config.AI_SKIP_CACHE_TTL)
        # Filter stage caches
        self._filter_cache = TTLCache(ttl_seconds=config.AI_FILTER_CACHE_TTL)
        self._filter_reject_cache = TTLCache(ttl_seconds=config.AI_FILTER_REJECT_CACHE_TTL)
        # Deep analysis stats
        self._total_calls = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._estimated_cost_usd = 0.0
        # Filter stats
        self._filter_calls = 0
        self._filter_input_tokens = 0
        self._filter_output_tokens = 0
        self._filter_cost_usd = 0.0
        self._load_stats()
```

- [ ] **Step 4: Update _load_stats and _save_stats for filter stats**

Replace `_load_stats` method:

```python
    def _load_stats(self) -> None:
        """Load persisted stats from disk."""
        for path, prefix in ((AI_STATS_FILE, ""), (AI_FILTER_STATS_FILE, "filter_")):
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    s = json.load(f)
                setattr(self, f"_{prefix}total_calls" if not prefix else f"_{prefix}calls",
                        s.get("total_calls", 0))
                setattr(self, f"_{prefix}total_input_tokens" if not prefix else f"_{prefix}input_tokens",
                        s.get("total_input_tokens", 0))
                setattr(self, f"_{prefix}total_output_tokens" if not prefix else f"_{prefix}output_tokens",
                        s.get("total_output_tokens", 0))
                cost_key = "estimated_cost_usd" if not prefix else "estimated_cost_usd"
                setattr(self, f"_{prefix}estimated_cost_usd" if not prefix else f"_{prefix}cost_usd",
                        s.get(cost_key, 0.0))
            except Exception as exc:
                logger.warning(f"Failed to load AI stats from {path}: {exc}")
```

Actually, this is getting over-engineered. Keep it simple — just add a second load/save pair:

```python
    def _load_stats(self) -> None:
        """Load persisted stats from disk."""
        if AI_STATS_FILE.exists():
            try:
                with open(AI_STATS_FILE, "r", encoding="utf-8") as f:
                    s = json.load(f)
                self._total_calls = s.get("total_calls", 0)
                self._total_input_tokens = s.get("total_input_tokens", 0)
                self._total_output_tokens = s.get("total_output_tokens", 0)
                self._estimated_cost_usd = s.get("estimated_cost_usd", 0.0)
            except Exception as exc:
                logger.warning(f"Failed to load deep AI stats: {exc}")
        if AI_FILTER_STATS_FILE.exists():
            try:
                with open(AI_FILTER_STATS_FILE, "r", encoding="utf-8") as f:
                    s = json.load(f)
                self._filter_calls = s.get("total_calls", 0)
                self._filter_input_tokens = s.get("total_input_tokens", 0)
                self._filter_output_tokens = s.get("total_output_tokens", 0)
                self._filter_cost_usd = s.get("estimated_cost_usd", 0.0)
            except Exception as exc:
                logger.warning(f"Failed to load filter AI stats: {exc}")
```

Replace `_save_stats` method:

```python
    def _save_stats(self) -> None:
        """Persist deep analysis stats to disk."""
        self._save_stats_to_file(AI_STATS_FILE, {
            "total_calls": self._total_calls,
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "estimated_cost_usd": self._estimated_cost_usd,
            "model": config.AI_DEEP_MODEL,
            "updated_at": utcnow().isoformat(),
        })

    def _save_filter_stats(self) -> None:
        """Persist filter stats to disk."""
        self._save_stats_to_file(AI_FILTER_STATS_FILE, {
            "total_calls": self._filter_calls,
            "total_input_tokens": self._filter_input_tokens,
            "total_output_tokens": self._filter_output_tokens,
            "estimated_cost_usd": self._filter_cost_usd,
            "model": config.AI_FILTER_MODEL,
            "updated_at": utcnow().isoformat(),
        })

    def _save_stats_to_file(self, path: Path, payload: dict) -> None:
        """Atomic write stats to a JSON file."""
        tmp_path = path.with_suffix(".json.tmp")
        try:
            path.parent.mkdir(exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception as exc:
            logger.error(f"Failed to persist AI stats to {path}: {exc}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
```

- [ ] **Step 5: Add filter() method**

After `_save_stats_to_file`, add the `filter()` method:

```python
    def filter(self, opp: "Opportunity") -> Optional[FilterResult]:
        """Stage 1: GPT-4o-mini quick filter. Returns cached result if seen before."""
        if not self._openai_client:
            # No OpenAI key — auto-pass everything to deep analysis
            return FilterResult(passed=True, reason="filter_disabled")

        cache_key = f"{opp.token_id}:{opp.side}:{opp.type}"

        # Check reject cache first (longer TTL)
        rejected = self._filter_reject_cache.get(cache_key)
        if rejected is not None:
            logger.debug(f"filter reject cache hit for {opp.market_id}")
            return rejected

        # Check pass cache
        cached = self._filter_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"filter cache hit for {opp.market_id}")
            return cached

        prompt = self._build_filter_prompt(opp)

        try:
            response = self._openai_client.chat.completions.create(
                model=config.AI_FILTER_MODEL,
                max_tokens=100,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You are a sports betting pre-screener. Return JSON with verdict and reason fields only."},
                    {"role": "user", "content": prompt},
                ],
            )

            content = response.choices[0].message.content or "{}"
            result_data = json.loads(content)
            verdict = str(result_data.get("verdict", "REJECT")).upper()
            reason = str(result_data.get("reason", ""))

            result = FilterResult(passed=(verdict == "PASS"), reason=reason)

            # Track tokens and cost
            usage = response.usage
            in_tok = usage.prompt_tokens if usage else 100
            out_tok = usage.completion_tokens if usage else 20
            call_cost = (
                in_tok * config.AI_FILTER_INPUT_PRICE_PER_MTOK
                + out_tok * config.AI_FILTER_OUTPUT_PRICE_PER_MTOK
            ) / 1_000_000
            self._filter_calls += 1
            self._filter_input_tokens += in_tok
            self._filter_output_tokens += out_tok
            self._filter_cost_usd += call_cost
            self._save_filter_stats()

            # Cache result
            if result.passed:
                self._filter_cache.set(cache_key, result)
            else:
                self._filter_reject_cache.set(cache_key, result)

            verdict_label = "\033[32mPASS\033[0m" if result.passed else "\033[31mREJECT\033[0m"
            logger.info(
                f"GPT [{verdict_label}] {opp.question[:50]}  "
                f"reason={reason[:60]}"
            )
            return result

        except Exception as e:
            logger.error(f"GPT filter failed for {opp.market_id}: {e}")
            # On error, pass through to deep analysis
            return FilterResult(passed=True, reason="filter_error_passthrough")
```

- [ ] **Step 6: Add _build_filter_prompt method**

After `filter()`, add:

```python
    def _build_filter_prompt(self, opp: "Opportunity") -> str:
        """Build a concise summary prompt for GPT-4o-mini filter."""
        ext = opp.external_odds
        odds_info = ""
        if ext:
            odds_info = (
                f"\nSportsbook consensus ({ext.bookmaker_count} bookmakers): "
                f"{ext.home_team}={ext.home_prob:.1%} / {ext.away_team}={ext.away_prob:.1%}"
            )
            if ext.draw_prob is not None:
                odds_info += f" / Draw={ext.draw_prob:.1%}"

        matchup = get_matchup_analysis_for_opportunity(opp)
        stats_info = ""
        if matchup and matchup.home_strength:
            hs = matchup.home_strength
            aws = matchup.away_strength
            stats_info = (
                f"\nRecent form ({matchup.lookback_matches} matches): "
                f"{hs.team_name}: GF={hs.weighted_goals_for:.1f} GA={hs.weighted_goals_against:.1f} WR={hs.win_rate:.0%}"
            )
            if aws:
                stats_info += (
                    f" | {aws.team_name}: GF={aws.weighted_goals_for:.1f} GA={aws.weighted_goals_against:.1f} WR={aws.win_rate:.0%}"
                )

        return f"""Quick screening: is this market worth deep analysis?

Market: {opp.question}
Candidate: BUY {opp.side} @ ${opp.price:.3f}
Edge: {opp.edge_pct:.1f}% ({opp.type})
End date: {opp.end_date.strftime('%Y-%m-%d %H:%M UTC')}
{odds_info}{stats_info}

Return JSON: {{"verdict": "PASS" or "REJECT", "reason": "one sentence"}}
PASS if the edge looks plausible and worth investigating further.
REJECT only if obviously bad: data mismatch, nonsensical edge, or clearly noise."""
```

- [ ] **Step 7: Commit**

```bash
git add ai_analyzer.py
git commit -m "feat: add GPT-4o-mini filter stage with dedup caching and cost tracking"
```

---

### Task 3: Update deep analysis to use Sonnet with model-anchored prompt

**Files:**
- Modify: `ai_analyzer.py:147-342` (analyze method and prompt)

- [ ] **Step 1: Update analyze() to use AI_DEEP_MODEL**

In `analyze()` method, change `config.AI_MODEL` to `config.AI_DEEP_MODEL` on line 169:

```python
            response = self._client.messages.create(
                model=config.AI_DEEP_MODEL,
```

- [ ] **Step 2: Update cost tracking in analyze() to use deep model pricing**

In `analyze()`, the cost calculation (lines 210-213) already uses `config.AI_INPUT_PRICE_PER_MTOK` which now points to `AI_DEEP_MODEL` pricing. No change needed.

- [ ] **Step 3: Update _build_prompt to anchor on model data**

Replace the prompt text in `_build_prompt` (lines 316-341) with:

```python
        return f"""You are a prediction market expert and sports analyst making trade decisions.

Market: {opp.question}
Current YES price: ${opp.yes_price:.3f} | NO price: ${opp.no_price:.3f}
{candidate_section}
Detected edge: {opp.edge_pct:.1f}% ({opp.type}) | Expected ROI if correct: {roi_edge:.1%}
End date: {opp.end_date.strftime('%Y-%m-%d %H:%M UTC')}
{odds_section}{bookmaker_note}
{matchup_section}{longshot_warning}

You have access to a structured match model that uses Poisson simulation with
recency-weighted xG, team form (last 8 matches, decay=0.82), head-to-head records,
and home/away advantage (12%). When the model data is present above, use its
computed probabilities as your PRIMARY ANCHOR. Your confidence should reflect the
data quality and edge reliability, not general uncertainty about sports outcomes.

For non-football sports without match model data, evaluate based on sportsbook
consensus quality (bookmaker count, line movement) and edge magnitude.

Decision criteria:
- If model fair probability > market price by a meaningful margin → recommend the candidate side
- If sportsbook consensus strongly supports the edge → recommend the candidate side
- Only SKIP if you have specific evidence AGAINST the trade, not general uncertainty
- Only recommend the OPPOSITE side if evidence clearly invalidates the candidate

For predicted_probability, return the estimated true probability of your recommended
side being correct, not the market YES probability unless you recommend YES.

Use the market_analysis tool to return your structured assessment."""
```

- [ ] **Step 4: Update confidence schema description**

In `_AI_SCHEMA` (line 36-38), change confidence description:

```python
        "confidence": {
            "type": "number",
            "description": "Confidence in this recommendation given the available data (0-1). Anchor to the match model confidence when available."
        },
```

- [ ] **Step 5: Update edge_detected description**

In `_AI_SCHEMA` (line 44-46):

```python
        "edge_detected": {
            "type": "boolean",
            "description": "Does the data support a real trading edge for the recommended side?"
        },
```

- [ ] **Step 6: Update analyze_test to use AI_DEEP_MODEL**

In `analyze_test()` (line 247):

```python
            resp = self._client.messages.create(
                model=config.AI_DEEP_MODEL,
```

- [ ] **Step 7: Update log_usage to show both stages**

Replace `log_usage` method:

```python
    def log_usage(self) -> None:
        logger.info(
            f"AI usage — filter: {self._filter_calls} calls ${self._filter_cost_usd:.4f} | "
            f"deep: {self._total_calls} calls ${self._estimated_cost_usd:.4f} | "
            f"total: ${self._filter_cost_usd + self._estimated_cost_usd:.4f}"
        )
```

- [ ] **Step 8: Commit**

```bash
git add ai_analyzer.py
git commit -m "feat: switch deep analysis to Sonnet with model-anchored prompt"
```

---

### Task 4: Wire 2-stage flow in main.py

**Files:**
- Modify: `main.py:511-619` (AI validation section)
- Modify: `main.py:69-78` (startup connectivity test)
- Modify: `main.py:136-148` (startup banner)

- [ ] **Step 1: Update startup banner**

In `main.py`, update the AI line in the startup banner (line 141):

```python
  {BOLD}AI filter{R}    {config.AI_FILTER_MODEL}  {DIM}({'ON' if config.OPENAI_API_KEY else 'OFF'}){R}
  {BOLD}AI deep{R}      {config.AI_DEEP_MODEL}  {DIM}(conf>={config.MIN_AI_CONFIDENCE:.0%}){R}
```

- [ ] **Step 2: Add OpenAI connectivity test at startup**

After the Claude API test block (lines 69-78), add:

```python
    # Test OpenAI API (filter stage)
    if config.OPENAI_API_KEY:
        logger.info("Testing OpenAI API connection...")
        try:
            import openai as _openai_test
            test_client = _openai_test.OpenAI(api_key=config.OPENAI_API_KEY)
            test_client.chat.completions.create(
                model=config.AI_FILTER_MODEL,
                max_tokens=5,
                messages=[{"role": "user", "content": "Say OK"}],
            )
            logger.info(f"  OpenAI API OK — model={config.AI_FILTER_MODEL}")
        except Exception as e:
            logger.warning(f"  OpenAI API test failed: {e} — filter stage disabled")
    else:
        logger.info("  OPENAI_API_KEY not set — filter stage disabled, all candidates go to deep analysis")
```

- [ ] **Step 3: Replace AI validation loop with 2-stage flow**

Replace the AI validation section starting at line 511 (`# 4. AI validation`). Replace lines 511-518 with:

```python
                # 4. AI validation (2-stage: GPT filter → Sonnet deep)
                validated = []
                advisory_block_count = 0
                filter_reject_count = 0
                for opp, verified_at in filtered:
                    if len(validated) >= config.AI_MAX_CANDIDATES:
                        break

                    # Stage 1: GPT-4o-mini filter
                    filter_result = ai.filter(opp)
                    if filter_result and not filter_result.passed:
                        filter_reject_count += 1
                        risk_journal.record(
                            cycle=cycle,
                            stage="ai_filter",
                            event="deny",
                            reason=f"gpt_reject:{filter_result.reason[:50]}",
                            opp=opp,
                        )
                        continue

                    # Stage 2: Sonnet deep analysis
                    analysis = ai.analyze(opp)
```

The rest of the AI validation logic (lines 518-619) stays the same — it handles `analysis` results.

Also add filter reject count to the cycle log. Find the existing log line that mentions `{len(filtered)} queued` (around line 473-479) and after the cycle log, add:

```python
                if filter_reject_count:
                    logger.info(f"  GPT filter rejected {filter_reject_count} candidates")
```

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: wire 2-stage AI pipeline in main loop (GPT filter → Sonnet deep)"
```

---

### Task 5: Update dashboard to show split AI costs

**Files:**
- Modify: `dashboard.py:25` (add filter stats file import)
- Modify: `dashboard.py:462` (load filter stats)
- Modify: `dashboard.py:1188-1220` (AI usage display)

- [ ] **Step 1: Add filter stats file path**

At top of `dashboard.py`, after the existing `AI_STATS_FILE` import (line 25), add:

```python
AI_FILTER_STATS_FILE = Path("data/ai_stats_filter.json")
```

- [ ] **Step 2: Load filter stats alongside deep stats**

Find where `ai_stats` is loaded (line 462: `ai_stats = load_json(AI_STATS_FILE)`). Add after it:

```python
ai_filter_stats = load_json(AI_FILTER_STATS_FILE)
```

- [ ] **Step 3: Replace AI usage display section**

Replace the AI usage section (lines 1188-1220) with:

```python
    # ── AI Usage + Realtime Feed ─────────────────────────────────────────────
    col_ai, col_feed = st.columns(2)

    with col_ai:
        st.html('<div class="section-hdr">// AI USAGE</div>')
        has_filter = bool(ai_filter_stats)
        has_deep = bool(ai_stats)

        if has_filter or has_deep:
            # Filter stats
            f_calls = (ai_filter_stats or {}).get("total_calls", 0) or 0
            f_cost = (ai_filter_stats or {}).get("estimated_cost_usd", 0) or 0
            f_model = (ai_filter_stats or {}).get("model", "—")
            f_in = (ai_filter_stats or {}).get("total_input_tokens", 0) or 0
            f_out = (ai_filter_stats or {}).get("total_output_tokens", 0) or 0

            # Deep stats
            d_calls = (ai_stats or {}).get("total_calls", 0) or 0
            d_cost = (ai_stats or {}).get("estimated_cost_usd", 0) or 0
            d_model = (ai_stats or {}).get("model", "—")
            d_in = (ai_stats or {}).get("total_input_tokens", 0) or 0
            d_out = (ai_stats or {}).get("total_output_tokens", 0) or 0

            total_cost = f_cost + d_cost
            total_calls = f_calls + d_calls

            cards_ai = st.columns(3)
            with cards_ai[0]:
                st.html(neon_stat_card(
                    f"FILTER ({f_model})" if f_model != "—" else "FILTER",
                    f"{f_calls:,} calls",
                    f"${f_cost:.4f} · {f_in:,}+{f_out:,} tok",
                    "c-blue",
                ))
            with cards_ai[1]:
                st.html(neon_stat_card(
                    f"DEEP ({d_model})" if d_model != "—" else "DEEP",
                    f"{d_calls:,} calls",
                    f"${d_cost:.4f} · {d_in:,}+{d_out:,} tok",
                    "c-green",
                ))
            with cards_ai[2]:
                st.html(neon_stat_card(
                    "TOTAL AI",
                    f"${total_cost:.4f}",
                    f"{total_calls:,} calls",
                    "c-amber",
                ))
        else:
            st.html(
                '<div style="color:#00ff4140;font-size:0.7rem;padding:20px 0;text-align:center">'
                '// NO AI DATA</div>',
            )
```

- [ ] **Step 4: Check if neon_stat_card supports "c-blue" color**

Search for the `neon_stat_card` function and check what color classes are supported. If `c-blue` doesn't exist, add it. The function likely maps class names to hex colors. Add blue (`#00aaff`) if missing.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "feat: split AI usage dashboard into filter + deep + total cost display"
```

---

### Task 6: Update .env.example and test full pipeline

**Files:**
- Modify: `.env.example` (if it exists) or `.env`

- [ ] **Step 1: Add OpenAI env vars to .env.example**

Add after the Anthropic section:

```
# OpenAI (filter stage)
OPENAI_API_KEY=sk-...
AI_FILTER_MODEL=gpt-4o-mini
AI_DEEP_MODEL=claude-sonnet-4-6
```

- [ ] **Step 2: Test imports**

Run: `python -c "import config; import ai_analyzer; print('OK')"`
Expected: `OK` with no errors

- [ ] **Step 3: Test full module load with both clients**

Run: `python -c "from ai_analyzer import AIAnalyzer, FilterResult; a = AIAnalyzer(); print(f'OpenAI: {a._openai_client is not None}'); print(f'Anthropic: {a._client is not None}')"`
Expected: Both `True` (if keys are set)

- [ ] **Step 4: Final commit**

```bash
git add .env.example
git commit -m "feat: add OpenAI env vars to .env.example"
```

---

## Summary

| Task | What | Key Files |
|------|------|-----------|
| 1 | Config + dependency | `config.py`, `requirements.txt` |
| 2 | GPT filter method + dedup cache | `ai_analyzer.py` |
| 3 | Sonnet deep analysis + anchored prompt | `ai_analyzer.py` |
| 4 | Wire 2-stage in main loop | `main.py` |
| 5 | Dashboard split cost display | `dashboard.py` |
| 6 | Env vars + integration test | `.env.example` |
