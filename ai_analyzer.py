"""
Claude AI Integration — validates arbitrage opportunities using structured output.
Rate-limited to 30 calls/min, results cached per market for 3 minutes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import anthropic
import openai

import config
from logger_setup import get_logger
from match_analytics import get_matchup_analysis_for_opportunity
from utils import RateLimiter, TTLCache, utcnow

AI_STATS_FILE = Path("data/ai_stats.json")
AI_FILTER_STATS_FILE = Path("data/ai_stats_filter.json")

if TYPE_CHECKING:
    from arbitrage import Opportunity

logger = get_logger(__name__)

# JSON schema for structured output
_AI_SCHEMA = {
    "type": "object",
    "properties": {
        "predicted_probability": {
            "type": "number",
            "description": "Estimated true probability (0-1)"
        },
        "confidence": {
            "type": "number",
            "description": "How confident in this prediction (0-1)"
        },
        "reasoning": {
            "type": "string",
            "description": "Brief reasoning"
        },
        "edge_detected": {
            "type": "boolean",
            "description": "Is the detected edge real or a data artifact?"
        },
        "recommended_side": {
            "type": "string",
            "enum": ["YES", "NO", "SKIP"],
            "description": "Which side to trade, or SKIP if no clear edge"
        },
        "risk_factors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Key risks to consider"
        }
    },
    "required": [
        "predicted_probability", "confidence", "reasoning",
        "edge_detected", "recommended_side", "risk_factors"
    ],
    "additionalProperties": False
}


@dataclass
class AIAnalysis:
    predicted_probability: float
    confidence: float
    reasoning: str
    edge_detected: bool
    recommended_side: str       # "YES" | "NO" | "SKIP"
    risk_factors: list[str]

    @property
    def is_valid(self) -> bool:
        if self.recommended_side == "SKIP":
            return False
        if self.confidence < config.MIN_AI_CONFIDENCE:
            return False
        # edge_detected is advisory — high confidence + clear side is enough
        if not self.edge_detected and self.confidence < 0.75:
            return False
        return True

    def supports_candidate(self, side: str, price: float) -> bool:
        return (
            self.is_valid
            and self.recommended_side == side
            and self.predicted_probability > price
        )


@dataclass
class FilterResult:
    passed: bool
    reason: str


class AIAnalyzer:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self._openai_client = (
            openai.OpenAI(api_key=config.OPENAI_API_KEY)
            if config.OPENAI_API_KEY
            else None
        )
        self._rate_limiter = RateLimiter(calls_per_minute=config.AI_CALLS_PER_MINUTE)
        self._cache = TTLCache(ttl_seconds=config.AI_CACHE_TTL)
        self._skip_cache = TTLCache(ttl_seconds=config.AI_SKIP_CACHE_TTL)
        self._filter_cache = TTLCache(ttl_seconds=config.AI_FILTER_CACHE_TTL)
        self._filter_reject_cache = TTLCache(ttl_seconds=config.AI_FILTER_REJECT_CACHE_TTL)
        self._total_calls = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._estimated_cost_usd = 0.0
        self._filter_calls = 0
        self._filter_input_tokens = 0
        self._filter_output_tokens = 0
        self._filter_cost_usd = 0.0
        self._load_stats()

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
                logger.warning(f"Failed to load AI stats from disk: {exc}")

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

    def _save_stats_to_file(self, path: Path, payload: dict) -> None:
        """Atomic write of a stats payload to a JSON file."""
        tmp_path = path.with_suffix(".json.tmp")
        try:
            path.parent.mkdir(exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception as exc:
            logger.error(f"Failed to persist stats to {path}: {exc}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    def _save_stats(self) -> None:
        """Persist deep-analysis stats to disk."""
        self._save_stats_to_file(AI_STATS_FILE, {
            "total_calls": self._total_calls,
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "estimated_cost_usd": self._estimated_cost_usd,
            "model": config.AI_DEEP_MODEL,
            "updated_at": utcnow().isoformat(),
        })

    def _save_filter_stats(self) -> None:
        """Persist GPT filter stats to disk."""
        self._save_stats_to_file(AI_FILTER_STATS_FILE, {
            "total_calls": self._filter_calls,
            "total_input_tokens": self._filter_input_tokens,
            "total_output_tokens": self._filter_output_tokens,
            "estimated_cost_usd": self._filter_cost_usd,
            "model": config.AI_FILTER_MODEL,
            "updated_at": utcnow().isoformat(),
        })

    def filter(self, opp: "Opportunity") -> Optional[FilterResult]:
        """Stage 1: GPT-4o-mini quick filter. Returns cached result if seen before."""
        if not self._openai_client:
            return FilterResult(passed=True, reason="filter_disabled")

        cache_key = f"{opp.token_id}:{opp.side}:{opp.type}"

        rejected = self._filter_reject_cache.get(cache_key)
        if rejected is not None:
            logger.debug(f"filter reject cache hit for {opp.market_id}")
            return rejected

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
            return FilterResult(passed=True, reason="filter_error_passthrough")

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

    def analyze(self, opp: "Opportunity") -> Optional[AIAnalysis]:
        """Analyze an opportunity. Returns None on failure or if API key missing."""
        if not config.ANTHROPIC_API_KEY:
            logger.debug("ANTHROPIC_API_KEY not set — skipping AI analysis")
            return None

        cache_key = f"{opp.token_id}:{opp.side}:{opp.type}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"AI cache hit for {opp.market_id}")
            return cached
        skipped = self._skip_cache.get(cache_key)
        if skipped is not None:
            logger.debug(f"AI skip cooldown active for {opp.market_id}")
            return skipped

        self._rate_limiter.wait_if_needed()

        prompt = self._build_prompt(opp)

        try:
            response = self._client.messages.create(
                model=config.AI_MODEL,
                max_tokens=config.AI_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                tools=[{
                    "name": "market_analysis",
                    "description": "Output structured market analysis",
                    "input_schema": _AI_SCHEMA
                }],
                tool_choice={"type": "tool", "name": "market_analysis"}
            )

            # Extract tool use result
            tool_result = None
            for block in response.content:
                if block.type == "tool_use" and block.name == "market_analysis":
                    tool_result = block.input
                    break

            if tool_result is None:
                # Fallback: try parsing text content as JSON
                for block in response.content:
                    if hasattr(block, "text"):
                        tool_result = json.loads(block.text)
                        break

            if not tool_result:
                logger.warning(f"AI returned no parseable result for {opp.market_id}")
                return None

            analysis = AIAnalysis(
                predicted_probability=float(tool_result.get("predicted_probability", 0.5)),
                confidence=float(tool_result.get("confidence", 0.0)),
                reasoning=str(tool_result.get("reasoning", "")),
                edge_detected=bool(tool_result.get("edge_detected", False)),
                recommended_side=str(tool_result.get("recommended_side", "SKIP")),
                risk_factors=list(tool_result.get("risk_factors") or []),
            )

            # Actual token usage from response.
            input_tok = response.usage.input_tokens if response.usage else 200
            output_tok = response.usage.output_tokens if response.usage else 100
            call_cost = (
                input_tok * config.AI_INPUT_PRICE_PER_MTOK
                + output_tok * config.AI_OUTPUT_PRICE_PER_MTOK
            ) / 1_000_000
            self._total_calls += 1
            self._total_input_tokens += input_tok
            self._total_output_tokens += output_tok
            self._estimated_cost_usd += call_cost
            self._save_stats()

            self._cache.set(cache_key, analysis)
            if not analysis.is_valid:
                self._skip_cache.set(cache_key, analysis)

            verdict = "\033[32mPASS\033[0m" if analysis.is_valid else "\033[33mSKIP\033[0m"
            logger.info(
                f"AI [{verdict}] {opp.question[:40]}  "
                f"conf={analysis.confidence:.0%}  "
                f"side={analysis.recommended_side}  "
                f"edge={'Y' if analysis.edge_detected else 'N'}"
            )
            return analysis

        except anthropic.RateLimitError:
            logger.warning("Anthropic rate limit hit — backing off")
            return None
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error for {opp.market_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"AI analysis failed for {opp.market_id}: {e}", exc_info=True)
            return None

    def analyze_test(self) -> Optional[AIAnalysis]:
        """Connectivity test — minimal API call."""
        try:
            resp = self._client.messages.create(
                model=config.AI_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "Say OK"}],
            )
            return AIAnalysis(
                predicted_probability=0.5,
                confidence=1.0,
                reasoning="connectivity test",
                edge_detected=False,
                recommended_side="SKIP",
                risk_factors=[],
            )
        except Exception as e:
            logger.error(f"Claude API connectivity test failed: {e}")
            return None

    def log_usage(self) -> None:
        logger.info(
            f"AI usage: {self._total_calls} calls | "
            f"in={self._total_input_tokens:,} tok | "
            f"out={self._total_output_tokens:,} tok | "
            f"cost=${self._estimated_cost_usd:.4f}"
        )

    def _build_prompt(self, opp: "Opportunity") -> str:
        ext = opp.external_odds
        odds_section = ""
        if ext:
            odds_section = (
                f"\nSportsbook consensus ({ext.bookmaker_count} bookmakers): "
                f"{ext.home_team}={ext.home_prob:.1%} / {ext.away_team}={ext.away_prob:.1%}"
            )
            if ext.draw_prob is not None:
                odds_section += f" / Draw={ext.draw_prob:.1%}"

        candidate_market_price = opp.price
        candidate_section = (
            f"\nCandidate trade: BUY {opp.side} @ ${candidate_market_price:.3f}"
        )

        matchup_section = ""
        matchup = get_matchup_analysis_for_opportunity(opp)
        if matchup is not None:
            candidate_true_prob = (
                matchup.yes_true_prob if opp.side == "YES" else matchup.no_true_prob
            )
            matchup_section = (
                "\n\nStructured match model context:\n"
                f"{matchup.to_prompt_block()}\n"
                f"- Candidate side fair probability: {candidate_true_prob:.1%}\n"
            )

        # Compute ROI-style edge for the prompt
        roi_edge = opp.edge_pct / max(0.10, 1.0 - opp.price) if opp.price < 1.0 else 0.0

        longshot_warning = ""
        if candidate_market_price < 0.20:
            longshot_warning = (
                "\n⚠ NOTE: This token is priced below $0.20. Apply extra scrutiny to "
                "edge calculations at low prices, as nominal edge percentages can be "
                "inflated. Evaluate the actual evidence before deciding."
            )

        bookmaker_note = ""
        if ext and ext.bookmaker_count > 0:
            bookmaker_note = f"\nBookmaker consensus based on {ext.bookmaker_count} bookmaker(s)."
            if ext.bookmaker_count < 5:
                bookmaker_note += " (low count — treat as less reliable)"

        return f"""You are a prediction market expert and sports analyst.

Market: {opp.question}
Current YES price: ${opp.yes_price:.3f} | NO price: ${opp.no_price:.3f}
{candidate_section}
Detected edge: {opp.edge_pct:.1f}% ({opp.type}) | Expected ROI if correct: {roi_edge:.1%}
End date: {opp.end_date.strftime('%Y-%m-%d %H:%M UTC')}
{odds_section}{bookmaker_note}
{matchup_section}{longshot_warning}

Assess whether the candidate trade has a real edge. Use the structured match model
when present, and do not recommend the opposite side unless the evidence is strong
enough to invalidate the candidate outright.

Consider:
- Does the candidate side's fair probability exceed its market price by a defensible margin?
- Do recent form, head-to-head, home/away split, shots on target, xG proxy, and lineup notes support the edge?
- Is the pricing discrepancy likely to persist until expiry?
- Are there upcoming events (injuries, news) that could affect the outcome?
- Is the market liquid enough for meaningful edge?
- For low-priced tokens (<$0.20): apply extra scrutiny but do not auto-reject — evaluate the actual evidence.

For predicted_probability, return the estimated true probability of your recommended
side being correct, not the market YES probability unless you recommend YES.

Use the market_analysis tool to return your structured assessment."""
