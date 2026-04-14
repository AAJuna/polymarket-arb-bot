"""
BTC 5-minute AI analyzer using Claude Haiku.

Sends 60 seconds of BTC price data to Haiku, which determines:
- Strategy to use (momentum, mean-reversion, volatility breakout, etc.)
- Direction: UP or DOWN
- Confidence level
- Reasoning
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from btc import config_btc as cfg
from logger_setup import get_logger

logger = get_logger(__name__)

_AI_SCHEMA = {
    "type": "object",
    "properties": {
        "side": {
            "type": "string",
            "enum": ["UP", "DOWN", "SKIP"],
            "description": "Predicted direction. SKIP if no clear signal.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Confidence in the prediction (0-1).",
        },
        "strategy": {
            "type": "string",
            "description": "Strategy used: momentum, mean_reversion, volatility_breakout, trend_following, or other.",
        },
        "reasoning": {
            "type": "string",
            "description": "Brief reasoning for the decision (1-2 sentences).",
        },
    },
    "required": ["side", "confidence", "strategy", "reasoning"],
}


@dataclass
class BtcAIAnalysis:
    side: str  # "UP", "DOWN", or "SKIP"
    confidence: float
    strategy: str
    reasoning: str

    @property
    def is_valid(self) -> bool:
        return (
            self.side in ("UP", "DOWN")
            and self.confidence >= cfg.MIN_CONFIDENCE
        )


AI_STATS_FILE = Path("data/btc/ai_stats.json")


class BtcAIAnalyzer:
    """Analyze BTC price data using Claude Haiku."""

    def __init__(self) -> None:
        self._client = None
        self._total_calls = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost = 0.0
        self._load_stats()

        api_key = cfg.ANTHROPIC_API_KEY if hasattr(cfg, "ANTHROPIC_API_KEY") else ""
        if not api_key:
            # Fall back to shared config
            import config
            api_key = config.ANTHROPIC_API_KEY

        if api_key:
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=api_key)
            except ImportError:
                logger.warning("anthropic package not installed — AI analysis disabled")

    def _load_stats(self) -> None:
        """Load accumulated stats from disk."""
        try:
            if AI_STATS_FILE.exists():
                with open(AI_STATS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._total_calls = data.get("total_calls", 0)
                self._total_input_tokens = data.get("total_input_tokens", 0)
                self._total_output_tokens = data.get("total_output_tokens", 0)
                self._total_cost = data.get("total_cost_usd", 0.0)
                logger.info(f"AI stats loaded: {self._total_calls} calls, ${self._total_cost:.4f}")
        except Exception:
            pass

    def _save_stats(self) -> None:
        """Persist accumulated stats to disk."""
        try:
            AI_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = AI_STATS_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "total_calls": self._total_calls,
                    "total_input_tokens": self._total_input_tokens,
                    "total_output_tokens": self._total_output_tokens,
                    "total_cost_usd": round(self._total_cost, 6),
                }, f, indent=2)
            tmp.replace(AI_STATS_FILE)
        except Exception:
            pass

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def analyze(
        self,
        price_history: list[tuple[float, float]],
        up_price: float,
        down_price: float,
        btc_price: float,
        time_remaining_sec: float,
        market_question: str,
        market_url: str = "",
    ) -> Optional[BtcAIAnalysis]:
        """Send price data to Haiku and get UP/DOWN/SKIP decision."""
        if not self._client:
            return None

        prompt = self._build_prompt(
            price_history, up_price, down_price, btc_price,
            time_remaining_sec, market_question, market_url,
        )

        try:
            response = self._client.messages.create(
                model=cfg.AI_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
                tools=[{
                    "name": "btc_analysis",
                    "description": "Output BTC 5-minute prediction",
                    "input_schema": _AI_SCHEMA,
                }],
                tool_choice={"type": "tool", "name": "btc_analysis"},
            )

            tool_result = None
            for block in response.content:
                if block.type == "tool_use" and block.name == "btc_analysis":
                    tool_result = block.input
                    break

            if not tool_result:
                logger.warning("AI returned no result")
                return None

            analysis = BtcAIAnalysis(
                side=str(tool_result.get("side", "SKIP")),
                confidence=float(tool_result.get("confidence", 0.0)),
                strategy=str(tool_result.get("strategy", "unknown")),
                reasoning=str(tool_result.get("reasoning", "")),
            )

            # Strategy filter: block strategies that lose money
            if analysis.side != "SKIP" and analysis.strategy in cfg.BLOCKED_STRATEGIES:
                logger.info(
                    f"AI blocked: strategy '{analysis.strategy}' is blacklisted  "
                    f"| original={analysis.side} conf={analysis.confidence:.0%}"
                )
                analysis.side = "SKIP"
                analysis.reasoning = f"Blocked strategy: {analysis.strategy}. {analysis.reasoning}"

            # Confidence filter: skip overconfident trades (0% WR at >70%)
            if analysis.side != "SKIP" and analysis.confidence > cfg.MAX_AI_CONFIDENCE:
                logger.info(
                    f"AI blocked: confidence {analysis.confidence:.0%} > "
                    f"cap {cfg.MAX_AI_CONFIDENCE:.0%}  "
                    f"| original={analysis.side} strategy={analysis.strategy}"
                )
                analysis.side = "SKIP"
                analysis.reasoning = f"Overconfident ({analysis.confidence:.0%}). {analysis.reasoning}"

            # Track costs (persisted to disk)
            input_tok = response.usage.input_tokens if response.usage else 200
            output_tok = response.usage.output_tokens if response.usage else 50
            self._total_calls += 1
            self._total_input_tokens += input_tok
            self._total_output_tokens += output_tok
            self._total_cost += (input_tok * 1.0 + output_tok * 5.0) / 1_000_000
            self._save_stats()

            # Explicit SKIP reason for diagnostics
            if analysis.is_valid:
                verdict = "PASS"
                skip_reason = ""
            else:
                verdict = "SKIP"
                if analysis.side == "SKIP":
                    # Check the reasoning prefix to find why
                    if analysis.reasoning.startswith("Blocked strategy"):
                        skip_reason = " [reason: blocked_strategy]"
                    elif analysis.reasoning.startswith("Overconfident"):
                        skip_reason = " [reason: max_conf_cap]"
                    else:
                        skip_reason = " [reason: haiku_returned_skip]"
                elif analysis.confidence < cfg.MIN_CONFIDENCE:
                    skip_reason = f" [reason: conf<{cfg.MIN_CONFIDENCE:.0%}]"
                else:
                    skip_reason = " [reason: unknown]"

            logger.info(
                f"AI [{verdict}]{skip_reason} {analysis.side}  conf={analysis.confidence:.0%}  "
                f"strategy={analysis.strategy}  | {analysis.reasoning[:60]}"
            )
            return analysis

        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            return None

    def _build_prompt(
        self,
        price_history: list[tuple[float, float]],
        up_price: float,
        down_price: float,
        btc_price: float,
        time_remaining_sec: float,
        market_question: str,
        market_url: str,
    ) -> str:
        # Format price data as compact table
        if price_history:
            t0 = price_history[0][0]
            prices_str = "\n".join(
                f"  t+{t - t0:5.1f}s  ${p:,.2f}"
                for t, p in price_history[::max(1, len(price_history) // 30)]  # sample ~30 points
            )
            first_price = price_history[0][1]
            last_price = price_history[-1][1]
            price_change = last_price - first_price
            pct_change = (price_change / first_price * 100) if first_price > 0 else 0
            high = max(p for _, p in price_history)
            low = min(p for _, p in price_history)
        else:
            prices_str = "  No data"
            price_change = 0
            pct_change = 0
            high = low = btc_price

        return f"""You are a BTC 5-minute prediction analyst for Polymarket.

MARKET: {market_question}
{f"URL: {market_url}" if market_url else ""}

CURRENT STATE:
- BTC Price: ${btc_price:,.2f}
- Polymarket Up Price: {up_price:.3f} (market thinks {up_price/(up_price+down_price)*100:.1f}% chance UP)
- Polymarket Down Price: {down_price:.3f}
- Time Remaining: {time_remaining_sec:.0f} seconds
- Window: 5 minutes total

PRICE DATA (last 60 seconds):
{prices_str}

SUMMARY:
- Change: ${price_change:+,.2f} ({pct_change:+.2f}%)
- High: ${high:,.2f}  Low: ${low:,.2f}  Range: ${high-low:,.2f}

TASK:
You MUST pick UP or DOWN. Analyze the 60-second price data and predict whether BTC will finish UP or DOWN relative to the start of this 5-minute window.

Choose your strategy (only two allowed — others are currently blocked):
- momentum: clear directional trend in the 60-second window — use this as your default
- trend_following: obvious multi-minute trend continuation from broader price action

Do NOT use btc_signal, volatility_breakout, mean_reversion, or microstructure.
Those strategies are currently disabled due to poor historical performance.

PREFER momentum. Only use trend_following if there's a strong multi-minute trend visible.

Rules:
- ALWAYS pick UP or DOWN. You are a trader, not an observer.
- SKIP is only allowed if price is EXACTLY flat AND market is exactly 50/50
- Set confidence 0.55-0.80 based on signal clarity
- Even a slight edge is worth trading — this is high-frequency, volume matters
- The market is often slow to react to micro-trends. Exploit that lag.
- Be decisive. A 55% edge traded 100 times is very profitable.
"""

    @property
    def stats(self) -> dict:
        return {
            "total_calls": self._total_calls,
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cost_usd": round(self._total_cost, 6),
        }
