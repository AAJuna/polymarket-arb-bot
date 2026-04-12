"""
BTC 5-minute bot configuration.
Reads from .env with BTC_ prefix. Independent from sports bot config.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Master switch
# ---------------------------------------------------------------------------
ENABLED: bool = os.getenv("BTC_ENABLED", "false").lower() == "true"
PAPER_TRADING: bool = os.getenv("BTC_PAPER_TRADING", "true").lower() == "true"

# ---------------------------------------------------------------------------
# AI (Claude Haiku)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL: str = os.getenv("BTC_AI_MODEL", "claude-haiku-4-5-20251001")
AI_COLLECT_SEC: float = float(os.getenv("BTC_AI_COLLECT_SEC", "60"))  # data collection window
AI_ENTRY_AT_SEC: float = float(os.getenv("BTC_AI_ENTRY_AT_SEC", "180"))  # execute at minute 3

# ---------------------------------------------------------------------------
# Polymarket (shared with sports bot)
# ---------------------------------------------------------------------------
PRIVATE_KEY: str = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_HOST: str = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
GAMMA_API_HOST: str = "https://gamma-api.polymarket.com"
CHAIN_ID: int = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE: int = int(os.getenv("SIGNATURE_TYPE", "0"))
FUNDER_ADDRESS: str | None = os.getenv("FUNDER_ADDRESS") or None

# ---------------------------------------------------------------------------
# RTDS WebSocket (BTC price feed)
# ---------------------------------------------------------------------------
RTDS_WS_URL: str = os.getenv("BTC_RTDS_WS_URL", "wss://ws-live-data.polymarket.com")
RTDS_PING_INTERVAL: float = float(os.getenv("BTC_RTDS_PING_INTERVAL", "2.0"))
RTDS_RECONNECT_BASE: float = 1.0
RTDS_RECONNECT_MAX: float = 30.0

# ---------------------------------------------------------------------------
# Signal engine
# ---------------------------------------------------------------------------
MIN_EDGE_PCT: float = float(os.getenv("BTC_MIN_EDGE_PCT", "3.0"))
MIN_CONFIDENCE: float = float(os.getenv("BTC_MIN_CONFIDENCE", "0.55"))
VOLATILITY_LOOKBACK_SEC: int = int(os.getenv("BTC_VOLATILITY_LOOKBACK_SEC", "1800"))
VOLATILITY_DEFAULT: float = float(os.getenv("BTC_VOLATILITY_DEFAULT", "0.50"))
VOLATILITY_FLOOR: float = float(os.getenv("BTC_VOLATILITY_FLOOR", "0.20"))
VOLATILITY_CEILING: float = float(os.getenv("BTC_VOLATILITY_CEILING", "2.00"))
STATISTICAL_WEIGHT: float = float(os.getenv("BTC_STATISTICAL_WEIGHT", "0.75"))
MOMENTUM_WEIGHT: float = float(os.getenv("BTC_MOMENTUM_WEIGHT", "0.15"))
ORDERFLOW_WEIGHT: float = float(os.getenv("BTC_ORDERFLOW_WEIGHT", "0.10"))
MOMENTUM_CAP: float = 0.05  # max +/-5% adjustment

# ---------------------------------------------------------------------------
# Trading parameters
# ---------------------------------------------------------------------------
MAX_POSITION_SIZE: float = float(os.getenv("BTC_MAX_POSITION_SIZE", "100.0"))
BET_SIZE_PCT: float = float(os.getenv("BTC_BET_SIZE_PCT", "2.0"))
ENTRY_DEADLINE_SEC: float = float(os.getenv("BTC_ENTRY_DEADLINE_SEC", "180.0"))
EXIT_BEFORE_END_SEC: float = float(os.getenv("BTC_EXIT_BEFORE_END_SEC", "30.0"))
MAX_CONCURRENT_WINDOWS: int = int(os.getenv("BTC_MAX_CONCURRENT_WINDOWS", "1"))

# ---------------------------------------------------------------------------
# Risk management (reuse sports bot thresholds by default)
# ---------------------------------------------------------------------------
DRAWDOWN_REDUCE_THRESHOLD: float = 0.20
DRAWDOWN_STOP_THRESHOLD: float = 0.40
CONSECUTIVE_LOSS_REDUCE: int = 5
CONSECUTIVE_LOSS_PAUSE: int = 10
PAUSE_DURATION_MINUTES: int = 30
DAILY_LOSS_LIMIT_PCT: float = 0.25

# ---------------------------------------------------------------------------
# Operational
# ---------------------------------------------------------------------------
POLL_INTERVAL_ACTIVE: float = float(os.getenv("BTC_POLL_INTERVAL_ACTIVE", "1.0"))
POLL_INTERVAL_IDLE: float = float(os.getenv("BTC_POLL_INTERVAL_IDLE", "5.0"))
SCANNER_CACHE_TTL: int = 30  # seconds — balance between speed and price freshness
LOG_LEVEL: str = os.getenv("BTC_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).upper()
PORTFOLIO_SUBDIR: str = "btc"

# ---------------------------------------------------------------------------
# Market WebSocket (CLOB order book — same as sports bot)
# ---------------------------------------------------------------------------
CLOB_WS_URL: str = os.getenv(
    "REALTIME_MARKET_WS_URL",
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
)


def validate() -> list[tuple[str, str]]:
    """Return startup configuration messages as (level, message) tuples."""
    issues: list[tuple[str, str]] = []
    if not PRIVATE_KEY or PRIVATE_KEY == "0x":
        issues.append(("warning", "POLYMARKET_PRIVATE_KEY is not set"))
    if PAPER_TRADING:
        issues.append(("info", "BTC_PAPER_TRADING=true -- no real orders will be placed"))
    return issues
