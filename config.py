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
AI_MODEL: str = os.getenv("AI_MODEL", "claude-sonnet-4-6")
AI_MAX_TOKENS: int = 512
AI_CALLS_PER_MINUTE: int = 30
AI_CACHE_TTL: int = 180  # seconds

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
ODDS_CACHE_TTL: int = 300  # seconds

# ---------------------------------------------------------------------------
# Trading parameters
# ---------------------------------------------------------------------------
MIN_EDGE_PCT: float = float(os.getenv("MIN_EDGE_PCT", "2.0"))
MIN_AI_CONFIDENCE: float = float(os.getenv("MIN_AI_CONFIDENCE", "0.70"))
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
DAILY_LOSS_LIMIT_PCT: float = 0.15        # -15% of day-start bankroll

# ---------------------------------------------------------------------------
# Market filters
# ---------------------------------------------------------------------------
MIN_VOLUME_24H: float = 500.0
MIN_LIQUIDITY: float = 200.0
MIN_HOURS_TO_EXPIRY: float = 2.0          # skip markets expiring in < 2 hours
SPORTS_KEYWORDS: list = [
    "nba", "nfl", "mlb", "nhl", "ncaa", "soccer", "tennis",
    "mma", "ufc", "boxing", "epl", "champions league", "atp",
    "cricket", "football", "basketball", "baseball", "hockey",
    "match", "game", " win ", " beat ", "score", "tournament",
    "playoff", "championship", "league", "cup", "series",
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

# Default fee rate if CLOB endpoint is unreachable
DEFAULT_FEE_RATE: float = 0.05

# Fee cache TTL
FEE_CACHE_TTL: int = 600  # 10 minutes


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
    return issues
