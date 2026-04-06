"""
Claude AI Integration — validates arbitrage opportunities using structured output.
Rate-limited to 30 calls/min, results cached per market for 3 minutes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import anthropic

import config
from logger_setup import get_logger
from utils import RateLimiter, TTLCache

AI_STATS_FILE = Path("data/ai_stats.json")

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
        return (
            self.confidence >= config.MIN_AI_CONFIDENCE
            and self.edge_detected
            and self.recommended_side != "SKIP"
        )


class AIAnalyzer:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self._rate_limiter = RateLimiter(calls_per_minute=config.AI_CALLS_PER_MINUTE)
        self._cache = TTLCache(ttl_seconds=config.AI_CACHE_TTL)
        self._total_calls = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._estimated_cost_usd = 0.0
        self._load_stats()

    def _load_stats(self) -> None:
        """Load persisted stats from disk."""
        if not AI_STATS_FILE.exists():
            return
        try:
            with open(AI_STATS_FILE, "r") as f:
                s = json.load(f)
            self._total_calls = s.get("total_calls", 0)
            self._total_input_tokens = s.get("total_input_tokens", 0)
            self._total_output_tokens = s.get("total_output_tokens", 0)
            self._estimated_cost_usd = s.get("estimated_cost_usd", 0.0)
        except Exception:
            pass

    def _save_stats(self) -> None:
        """Persist stats to disk."""
        try:
            AI_STATS_FILE.parent.mkdir(exist_ok=True)
            with open(AI_STATS_FILE, "w") as f:
                json.dump({
                    "total_calls": self._total_calls,
                    "total_input_tokens": self._total_input_tokens,
                    "total_output_tokens": self._total_output_tokens,
                    "estimated_cost_usd": self._estimated_cost_usd,
                    "model": config.AI_MODEL,
                    "updated_at": __import__("datetime").datetime.utcnow().isoformat(),
                }, f, indent=2)
        except Exception:
            pass

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

            # Actual token usage from response
            # claude-sonnet-4-6: $3/MTok input, $15/MTok output
            input_tok = response.usage.input_tokens if response.usage else 200
            output_tok = response.usage.output_tokens if response.usage else 100
            call_cost = (input_tok * 3 + output_tok * 15) / 1_000_000
            self._total_calls += 1
            self._total_input_tokens += input_tok
            self._total_output_tokens += output_tok
            self._estimated_cost_usd += call_cost
            self._save_stats()

            self._cache.set(cache_key, analysis)

            logger.info(
                f"AI: {opp.question[:45]} | "
                f"conf={analysis.confidence:.2f} "
                f"side={analysis.recommended_side} "
                f"edge={analysis.edge_detected} "
                f"({'PASS' if analysis.is_valid else 'SKIP'})"
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

        return f"""You are a prediction market expert and sports analyst.

Market: {opp.question}
Current YES price: ${opp.yes_price:.3f} | NO price: ${opp.no_price:.3f}
Detected edge: {opp.edge_pct:.1f}% ({opp.type})
End date: {opp.end_date.strftime('%Y-%m-%d %H:%M UTC')}
{odds_section}

Assess whether this edge is real or a data artifact. Consider:
- Is the pricing discrepancy likely to persist until expiry?
- Are there upcoming events (injuries, news) that could affect the outcome?
- Is the market liquid enough for meaningful edge?

Use the market_analysis tool to return your structured assessment."""
