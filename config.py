"""
Central configuration — loads all settings from .env.
Every other module imports from here; never read os.environ directly elsewhere.
"""

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Polymarket / Polygon
# ---------------------------------------------------------------------------
PRIVATE_KEY: str = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_HOST: str = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
GAMMA_API_HOST: str = "https://gamma-api.polymarket.com"
DATA_API_HOST: str = "https://data-api.polymarket.com"
CHAIN_ID: int = int(os.getenv("CHAIN_ID", "137"))
SIGNATURE_TYPE: int = int(os.getenv("SIGNATURE_TYPE", "0"))
FUNDER_ADDRESS: Optional[str] = os.getenv("FUNDER_ADDRESS") or None

# Polygon USDC contract (6 decimals)
USDC_ADDRESS: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
# Conditional Token Framework contract
CTF_ADDRESS: str = "0x4bFb41d5B3570DeFd03C39a9A4D8DE6Bd8B8982E"
# CLOB Exchange contract (must be approved as USDC spender)
EXCHANGE_ADDRESS: str = "0x4bFb41d5B3570DeFd03C39a9A4D8DE6Bd8B8982E"

# ---------------------------------------------------------------------------
# Anthropic Claude
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL: str = os.getenv("AI_MODEL", "claude-haiku-4-5-20251001")
AI_MAX_TOKENS: int = 512
AI_CALLS_PER_MINUTE: int = 30
AI_CACHE_TTL: int = 180  # seconds
AI_SKIP_CACHE_TTL: int = int(os.getenv("AI_SKIP_CACHE_TTL", "600"))
AI_MAX_CANDIDATES: int = int(os.getenv("AI_MAX_CANDIDATES", "2"))
AI_SCAN_LIMIT: int = int(os.getenv("AI_SCAN_LIMIT", "5"))
AI_MIN_EDGE_PCT: float = float(os.getenv("AI_MIN_EDGE_PCT", "6.0"))
AI_PAPER_MODE: str = os.getenv("AI_PAPER_MODE", "gate").strip().lower()  # gate | advisory


def _ai_pricing_per_mtok(model: str) -> tuple[float, float]:
    """Return approximate input/output USD per 1M tokens for dashboard cost tracking."""
    model = model.lower()
    if "haiku-4-5" in model:
        return 1.0, 5.0
    if "sonnet" in model:
        return 3.0, 15.0
    if "opus" in model:
        return 15.0, 75.0
    if "haiku" in model:
        return 0.25, 1.25
    return 3.0, 15.0


AI_INPUT_PRICE_PER_MTOK, AI_OUTPUT_PRICE_PER_MTOK = _ai_pricing_per_mtok(AI_MODEL)

# ---------------------------------------------------------------------------
# The Odds API
# ---------------------------------------------------------------------------
ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE: str = "https://api.the-odds-api.com/v4"
ODDS_SPORTS: list = [
    "americanfootball_nfl",
    "basketball_nba",
    "baseball_mlb",
    "icehockey_nhl",
    "soccer_epl",
    "soccer_usa_mls",
    "mma_mixed_martial_arts",
    "americanfootball_ncaaf",
    "basketball_ncaab",
    "tennis_atp_french_open",
    "soccer_uefa_champs_league",
]
ODDS_CACHE_TTL: int = int(os.getenv("ODDS_CACHE_TTL", "900"))  # seconds (15 min default)
MIN_BOOKMAKER_COUNT: int = int(os.getenv("MIN_BOOKMAKER_COUNT", "3"))
SPORTMONKS_API_KEY: str = os.getenv("SPORTMONKS_API_KEY", "")
SPORTMONKS_API_BASE: str = os.getenv("SPORTMONKS_API_BASE", "https://api.sportmonks.com/v3/football")
API_FOOTBALL_API_KEY: str = os.getenv("API_FOOTBALL_API_KEY", "")
API_FOOTBALL_API_BASE: str = os.getenv("API_FOOTBALL_API_BASE", "https://v3.football.api-sports.io")

# ---------------------------------------------------------------------------
# Trading parameters
# ---------------------------------------------------------------------------
MIN_EDGE_PCT: float = float(os.getenv("MIN_EDGE_PCT", "2.0"))
MIN_AI_CONFIDENCE: float = float(os.getenv("MIN_AI_CONFIDENCE", "0.60"))
BET_SIZE_PCT: float = float(os.getenv("BET_SIZE_PCT", "2.0"))
INITIAL_BET_SIZE: float = float(os.getenv("INITIAL_BET_SIZE", "2.0"))
MAX_BET_SIZE: float = float(os.getenv("MAX_BET_SIZE", "50.0"))
MAX_EXPOSURE_PCT: float = float(os.getenv("MAX_EXPOSURE_PCT", "30.0"))
MAX_MARKET_CONCENTRATION_PCT: float = 10.0

# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------
DRAWDOWN_REDUCE_THRESHOLD: float = 0.20   # -20% from peak → halve bet size
DRAWDOWN_STOP_THRESHOLD: float = 0.40     # -40% from peak → full stop
CONSECUTIVE_LOSS_REDUCE: int = 5          # halve bet size
CONSECUTIVE_LOSS_PAUSE: int = 10          # pause trading
PAUSE_DURATION_MINUTES: int = 30
DAILY_LOSS_LIMIT_PCT: float = 0.25        # -25% of day-start bankroll

# ---------------------------------------------------------------------------
# Market filters
# ---------------------------------------------------------------------------
MIN_VOLUME_24H: float = 500.0
MIN_LIQUIDITY: float = 200.0
MIN_HOURS_TO_EXPIRY: float = 2.0          # skip markets expiring in < 2 hours
ENABLE_SAME_MARKET_ARB: bool = os.getenv("ENABLE_SAME_MARKET_ARB", "true").lower() == "true"
ENABLE_SAME_MARKET_EXECUTION: bool = os.getenv("ENABLE_SAME_MARKET_EXECUTION", "false").lower() == "true"
ENABLE_CROSS_MARKET_ARB: bool = os.getenv("ENABLE_CROSS_MARKET_ARB", "false").lower() == "true"
ENABLE_ODDS_COMPARISON_ARB: bool = os.getenv("ENABLE_ODDS_COMPARISON_ARB", "true").lower() == "true"
ODDS_COMPARISON_MONEYLINE_ONLY: bool = os.getenv("ODDS_COMPARISON_MONEYLINE_ONLY", "true").lower() == "true"
ODDS_COMPARISON_MIN_PRICE: float = float(os.getenv("ODDS_COMPARISON_MIN_PRICE", "0.15"))
MATCH_ANALYTICS_ENABLED: bool = os.getenv("MATCH_ANALYTICS_ENABLED", "true").lower() == "true"
MATCH_DATA_PROVIDERS: list[str] = [
    item.strip().lower()
    for item in os.getenv(
        "MATCH_DATA_PROVIDERS",
        "sportmonks,api_football,sportsbook_only",
    ).split(",")
    if item.strip()
]
MATCH_ANALYTICS_CACHE_TTL: int = int(os.getenv("MATCH_ANALYTICS_CACHE_TTL", "900"))
MATCH_LOOKBACK_DAYS: int = int(os.getenv("MATCH_LOOKBACK_DAYS", "365"))
MATCH_LOOKBACK_MATCHES: int = int(os.getenv("MATCH_LOOKBACK_MATCHES", "8"))
MATCH_RECENCY_DECAY: float = float(os.getenv("MATCH_RECENCY_DECAY", "0.82"))
MATCH_HOME_ADVANTAGE: float = float(os.getenv("MATCH_HOME_ADVANTAGE", "0.12"))
MATCH_XG_PER_SHOT_ON_TARGET: float = float(os.getenv("MATCH_XG_PER_SHOT_ON_TARGET", "0.32"))
MATCH_LINEUP_ABSENCE_PENALTY: float = float(os.getenv("MATCH_LINEUP_ABSENCE_PENALTY", "0.025"))
MATCH_HEAD_TO_HEAD_WEIGHT: float = float(os.getenv("MATCH_HEAD_TO_HEAD_WEIGHT", "0.03"))
SPORTMONKS_DAILY_LIMIT: int = int(os.getenv("SPORTMONKS_DAILY_LIMIT", "0"))
API_FOOTBALL_DAILY_LIMIT: int = int(os.getenv("API_FOOTBALL_DAILY_LIMIT", "100"))
SPORTS_KEYWORDS: list = [
    "nba", "nfl", "mlb", "nhl", "ncaa", "soccer", "tennis",
    "mma", "ufc", "boxing", "epl", "champions league", "atp",
    "cricket", "football", "basketball", "baseball", "hockey",
    "match", "game", " win ", " beat ", "score", "tournament",
    "playoff", "championship", "league", "cup", "series",
    "chess", "fide", "magnus", "grandmaster",
]

# ---------------------------------------------------------------------------
# Operational
# ---------------------------------------------------------------------------
POLL_INTERVAL: int = int(os.getenv("POLL_INTERVAL", "7"))
STALE_ORDER_TIMEOUT: int = 30             # seconds before cancelling unfilled orders
MAX_CONCURRENT_ORDERS: int = 10
PORTFOLIO_SAVE_INTERVAL: int = 300        # 5 minutes
PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
RESET_STATE_ON_START: bool = os.getenv("RESET_STATE_ON_START", "false").lower() == "true"
RESET_LOGS_ON_START: bool = os.getenv("RESET_LOGS_ON_START", "true").lower() == "true"
REALTIME_MARKET_WS_ENABLED: bool = os.getenv("REALTIME_MARKET_WS_ENABLED", "true").lower() == "true"
REALTIME_MARKET_WS_URL: str = os.getenv(
    "REALTIME_MARKET_WS_URL",
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
)
REALTIME_MARKET_WS_MAX_ASSETS: int = int(os.getenv("REALTIME_MARKET_WS_MAX_ASSETS", "400"))
REALTIME_MARKET_WS_MAX_HOURS_TO_EXPIRY: float = float(
    os.getenv("REALTIME_MARKET_WS_MAX_HOURS_TO_EXPIRY", "24.0")
)
REALTIME_MARKET_WS_QUOTE_TTL_SECONDS: float = float(
    os.getenv("REALTIME_MARKET_WS_QUOTE_TTL_SECONDS", "12.0")
)
REALTIME_MARKET_WS_BOOK_TTL_SECONDS: float = float(
    os.getenv("REALTIME_MARKET_WS_BOOK_TTL_SECONDS", "20.0")
)
ENABLE_REALTIME_EXECUTION_GATE: bool = os.getenv("ENABLE_REALTIME_EXECUTION_GATE", "true").lower() == "true"
REALTIME_GATE_MAX_SPREAD: float = float(os.getenv("REALTIME_GATE_MAX_SPREAD", "0.15"))
REALTIME_GATE_MIN_DEPTH_USD: float = float(os.getenv("REALTIME_GATE_MIN_DEPTH_USD", "5.0"))

# Default fee rate if CLOB endpoint is unreachable
DEFAULT_FEE_RATE: float = 0.05

# Fee cache TTL
FEE_CACHE_TTL: int = 600  # 10 minutes

# ---------------------------------------------------------------------------
# Arbitrage hardening
# ---------------------------------------------------------------------------
# Same-market: total cost must be below this threshold (not just < 1.0)
SAME_MARKET_COST_THRESHOLD: float = float(os.getenv("SAME_MARKET_COST_THRESHOLD", "0.985"))
# Reject a price that was fetched more than this many milliseconds ago
MAX_STALE_MS: int = int(os.getenv("MAX_STALE_MS", "2000"))
# Minimum ask-side depth (USD) required to emit a same-market opportunity
MIN_LIQUIDITY_DEPTH_USD: float = float(os.getenv("MIN_LIQUIDITY_DEPTH_USD", "500.0"))
# Cross-market: skip group if computed confidence falls below this
CROSS_MARKET_CONFIDENCE_THRESHOLD: float = float(os.getenv("CROSS_MARKET_CONFIDENCE_THRESHOLD", "0.80"))
# Odds comparison: skip match if fuzzy-match confidence falls below this
ODDS_MATCH_MIN_CONFIDENCE: float = float(os.getenv("ODDS_MATCH_MIN_CONFIDENCE", "0.85"))
# Target trade size (USD) used for size-aware depth simulation
TRADE_SIZE_TARGET_USD: float = float(os.getenv("TRADE_SIZE_TARGET_USD", "100.0"))


def validate() -> list[str]:
    """Return a list of missing/invalid configuration warnings."""
    issues = []
    if not PRIVATE_KEY or PRIVATE_KEY == "0x":
        issues.append("POLYMARKET_PRIVATE_KEY is not set")
    if not ANTHROPIC_API_KEY or not ANTHROPIC_API_KEY.startswith("sk-"):
        issues.append("ANTHROPIC_API_KEY is not set or looks invalid")
    if not ODDS_API_KEY:
        issues.append("ODDS_API_KEY not set — odds comparison arbitrage will be disabled")
    if PAPER_TRADING:
        issues.append("PAPER_TRADING=true — no real orders will be placed")
    if RESET_STATE_ON_START:
        issues.append("RESET_STATE_ON_START=true — persisted state will be cleared at startup")
    if ENABLE_SAME_MARKET_ARB and not ENABLE_SAME_MARKET_EXECUTION:
        issues.append("same-market arb detection enabled but execution disabled until bundle execution is atomic")
    if not PAPER_TRADING and AI_PAPER_MODE != "gate":
        issues.append("AI_PAPER_MODE is ignored in live mode — AI remains a hard gate")
    if PAPER_TRADING and AI_PAPER_MODE == "advisory":
        issues.append("AI_PAPER_MODE=advisory no longer bypasses low-confidence or side-mismatch AI denials")
    if MATCH_ANALYTICS_ENABLED and not any((SPORTMONKS_API_KEY, API_FOOTBALL_API_KEY)):
        issues.append(
            "No football stats provider key set — advanced team analytics will use sportsbook-only fallback"
        )
    return issues
