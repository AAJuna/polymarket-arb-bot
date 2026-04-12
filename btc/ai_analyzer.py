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


class BtcAIAnalyzer:
    """Analyze BTC price data using Claude Haiku."""

    def __init__(self) -> None:
        self._client = None
        self._total_calls = 0
        self._total_cost = 0.0

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

            # Track costs
            self._total_calls += 1
            input_tok = response.usage.input_tokens if response.usage else 200
            output_tok = response.usage.output_tokens if response.usage else 50
            self._total_cost += (input_tok * 1.0 + output_tok * 5.0) / 1_000_000

            verdict = "PASS" if analysis.is_valid else "SKIP"
            logger.info(
                f"AI [{verdict}] {analysis.side}  conf={analysis.confidence:.0%}  "
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
Analyze the price data and determine if BTC will be UP or DOWN at the end of this 5-minute window compared to the start price.

Choose your own strategy based on what the data shows:
- momentum: price trending clearly in one direction
- mean_reversion: price moved too far too fast, likely to reverse
- volatility_breakout: price breaking out of a range
- trend_following: established trend continuing
- or describe your own strategy

Rules:
- Only predict UP or DOWN if you have reasonable confidence
- SKIP if the data is too noisy or unclear
- The market price already reflects crowd consensus — you need edge
- Consider: is the market price wrong? Why?
"""

    @property
    def stats(self) -> dict:
        return {
            "total_calls": self._total_calls,
            "total_cost_usd": round(self._total_cost, 4),
        }
