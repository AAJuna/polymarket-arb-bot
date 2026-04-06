"""
Shared utilities: retry decorator, rate limiter, TTL cache, price helpers, timestamps.
No domain knowledge — pure infrastructure.
"""

import functools
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple, Type


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(s: str) -> datetime:
    """Parse ISO-8601 string → timezone-aware datetime."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def seconds_until(dt: datetime) -> float:
    """Seconds remaining until a future datetime (negative if past)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - utcnow()).total_seconds()


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """Exponential backoff retry decorator.

    Usage:
        @retry(max_attempts=3, base_delay=1.0, exceptions=(requests.RequestException,))
        def call_api(): ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            import logging
            logger = logging.getLogger(func.__module__)
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay *= 2
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Rate limiter (token bucket)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Thread-safe token bucket rate limiter.

    Example:
        limiter = RateLimiter(calls_per_minute=30)
        limiter.wait_if_needed()  # blocks until a token is available
    """

    def __init__(self, calls_per_minute: int):
        self._interval = 60.0 / calls_per_minute  # seconds per call
        self._lock = threading.Lock()
        self._last_call: float = 0.0

    def wait_if_needed(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------

class TTLCache:
    """Simple in-memory key-value cache with time-to-live expiry.

    Example:
        cache = TTLCache(ttl_seconds=180)
        cache.set("key", value)
        result = cache.get("key")  # None if expired
    """

    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._store: dict[str, Tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic() + self._ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# ---------------------------------------------------------------------------
# Price / odds helpers
# ---------------------------------------------------------------------------

def american_odds_to_probability(odds: int) -> float:
    """Convert American moneyline odds to implied probability (0-1)."""
    if odds > 0:
        return 100.0 / (odds + 100.0)
    else:
        return abs(odds) / (abs(odds) + 100.0)


def decimal_odds_to_probability(odds: float) -> float:
    """Convert decimal odds (e.g. 1.5) to implied probability."""
    return 1.0 / odds if odds > 0 else 0.0


def devig_probabilities(*raw_probs: float) -> list[float]:
    """Remove bookmaker's vig so probabilities sum to 1.0."""
    total = sum(raw_probs)
    if total <= 0:
        return list(raw_probs)
    return [p / total for p in raw_probs]


def polymarket_fee(price: float, fee_rate: float) -> float:
    """Polymarket fee per share: feeRate * p * (1 - p)."""
    return fee_rate * price * (1.0 - price)


def fee_adjusted_cost(price: float, fee_rate: float) -> float:
    """Total cost to acquire 1 share including Polymarket fee."""
    return price + polymarket_fee(price, fee_rate)
