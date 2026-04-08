"""
External data feeds — pulls sportsbook odds from The Odds API for cross-reference
with Polymarket prices (odds comparison arbitrage).
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

import config
from logger_setup import get_logger
from utils import TTLCache, american_odds_to_probability, devig_probabilities, parse_iso, retry, utcnow

logger = get_logger(__name__)

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

_odds_cache = TTLCache(ttl_seconds=config.ODDS_CACHE_TTL)

_GENERIC_TEAM_TOKENS = {
    "a",
    "ac",
    "afc",
    "bc",
    "bk",
    "cf",
    "club",
    "de",
    "fc",
    "fk",
    "if",
    "sc",
    "the",
    "uc",
}


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------

@dataclass
class ExternalOdds:
    event_key: str          # normalized "team_a_vs_team_b_YYYYMMDD"
    home_team: str
    away_team: str
    sport: str
    commence_time: datetime
    bookmaker_count: int    # how many books averaged
    home_prob: float        # de-vigged consensus probability
    away_prob: float
    draw_prob: Optional[float]
    source: str = "theoddsapi"
    fetched_at: datetime = None
    match_confidence: float = 0.0   # 0–1; how well this entry matched the Polymarket question

    def __post_init__(self):
        if self.fetched_at is None:
            self.fetched_at = utcnow()


# ---------------------------------------------------------------------------
# The Odds API fetcher
# ---------------------------------------------------------------------------

@retry(max_attempts=3, base_delay=2.0, exceptions=(requests.RequestException,))
def _fetch_sport_odds(sport: str) -> list[dict]:
    if not config.ODDS_API_KEY:
        return []

    url = f"{config.ODDS_API_BASE}/sports/{sport}/odds/"
    params = {
        "apiKey": config.ODDS_API_KEY,
        "regions": "us,eu",
        "markets": "h2h",
        "oddsFormat": "american",
    }
    resp = _session.get(url, params=params, timeout=10)
    if resp.status_code == 422:
        logger.debug(f"No odds available for sport: {sport}")
        return []
    resp.raise_for_status()
    return resp.json()


def fetch_all_odds() -> list[ExternalOdds]:
    """Fetch and normalize odds from all configured sports."""
    cached = _odds_cache.get("all_odds")
    if cached is not None:
        return cached

    if not config.ODDS_API_KEY:
        logger.debug("ODDS_API_KEY not set — odds comparison disabled")
        return []

    all_odds: list[ExternalOdds] = []

    for sport in config.ODDS_SPORTS:
        try:
            raw_events = _fetch_sport_odds(sport)
            for event in raw_events:
                odds = _parse_event(event, sport)
                if odds:
                    all_odds.append(odds)
        except Exception as e:
            logger.warning(f"Failed to fetch odds for {sport}: {e}")

    logger.info(f"Fetched odds for {len(all_odds)} events from The Odds API")
    _odds_cache.set("all_odds", all_odds)
    return all_odds


def _parse_event(raw: dict, sport: str) -> Optional[ExternalOdds]:
    """Parse a single Odds API event → ExternalOdds."""
    try:
        home_team = raw.get("home_team", "")
        away_team = raw.get("away_team", "")
        commence_str = raw.get("commence_time", "")
        if not home_team or not away_team or not commence_str:
            return None

        commence_time = parse_iso(commence_str)

        # Aggregate probabilities across bookmakers
        home_probs, away_probs, draw_probs = [], [], []

        for bookmaker in raw.get("bookmakers") or []:
            for market in bookmaker.get("markets") or []:
                if market.get("key") != "h2h":
                    continue
                outcomes = {o["name"]: o["price"] for o in market.get("outcomes") or []}
                home_odds = outcomes.get(home_team)
                away_odds = outcomes.get(away_team)
                draw_odds = outcomes.get("Draw")

                if home_odds is None or away_odds is None:
                    continue

                # Raw implied probabilities
                hp = american_odds_to_probability(int(home_odds))
                ap = american_odds_to_probability(int(away_odds))
                dp = american_odds_to_probability(int(draw_odds)) if draw_odds else None

                # De-vig
                if dp is not None:
                    hp, ap, dp = devig_probabilities(hp, ap, dp)
                else:
                    hp, ap = devig_probabilities(hp, ap)

                home_probs.append(hp)
                away_probs.append(ap)
                if dp is not None:
                    draw_probs.append(dp)

        if not home_probs:
            return None

        avg_home = sum(home_probs) / len(home_probs)
        avg_away = sum(away_probs) / len(away_probs)
        avg_draw = sum(draw_probs) / len(draw_probs) if draw_probs else None

        event_key = _make_event_key(home_team, away_team, commence_time)

        return ExternalOdds(
            event_key=event_key,
            home_team=home_team,
            away_team=away_team,
            sport=sport,
            commence_time=commence_time,
            bookmaker_count=len(home_probs),
            home_prob=avg_home,
            away_prob=avg_away,
            draw_prob=avg_draw,
        )
    except Exception as e:
        logger.debug(f"Failed to parse odds event: {e}")
        return None


# ---------------------------------------------------------------------------
# Market matching (fuzzy)
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _distinctive_tokens(text: str) -> set[str]:
    """Return team tokens that are useful for fuzzy matching."""
    return {
        token
        for token in _normalize(text).split()
        if len(token) > 1 and token not in _GENERIC_TEAM_TOKENS
    }


def _make_event_key(home: str, away: str, dt: datetime) -> str:
    h = _normalize(home).replace(" ", "_")
    a = _normalize(away).replace(" ", "_")
    d = dt.strftime("%Y%m%d")
    return f"{h}_vs_{a}_{d}"


def match_team_side(question: str, odds: ExternalOdds) -> Optional[str]:
    """Return home/away/draw if the market question identifies one outcome."""
    q_norm = _normalize(question)
    q_tokens = _distinctive_tokens(question)

    if "draw" in q_norm:
        return "draw"

    home_tokens = _distinctive_tokens(odds.home_team)
    away_tokens = _distinctive_tokens(odds.away_team)
    home_hit = bool(home_tokens and home_tokens <= q_tokens)
    away_hit = bool(away_tokens and away_tokens <= q_tokens)

    if home_hit and not away_hit:
        return "home"
    if away_hit and not home_hit:
        return "away"
    return None


def get_odds_for_market(
    question: str,
    end_date: datetime,
    context_text: str = "",
) -> Optional[ExternalOdds]:
    """Fuzzy-match a Polymarket question to an ExternalOdds entry.

    Returns the best match (with match_confidence set) if:
    - Both team names appear in the question
    - Event commences within 24 hours of end_date
    Otherwise returns None.
    """
    all_odds = fetch_all_odds()
    if not all_odds:
        return None

    match_text = f"{question} {context_text}".strip()
    match_words = _distinctive_tokens(match_text)
    best: Optional[ExternalOdds] = None
    best_score = 0
    best_confidence = 0.0

    for odds in all_odds:
        # Check team names
        home_words = _distinctive_tokens(odds.home_team)
        away_words = _distinctive_tokens(odds.away_team)
        if not home_words or not away_words:
            continue

        home_overlap = len(home_words & match_words)
        away_overlap = len(away_words & match_words)

        if home_overlap == 0 or away_overlap == 0:
            continue

        # Check time proximity (within 24 hours)
        time_diff = abs((odds.commence_time - end_date).total_seconds())
        if time_diff > 86400:
            continue

        score = home_overlap + away_overlap

        # Confidence score: token overlap ratio (70%) + time proximity (30%)
        home_ratio = home_overlap / max(1, len(home_words))
        away_ratio = away_overlap / max(1, len(away_words))
        token_ratio = min(home_ratio, away_ratio)
        hours_diff = time_diff / 3600.0
        time_proximity = max(0.0, 1.0 - hours_diff / 24.0)
        confidence = token_ratio * 0.7 + time_proximity * 0.3

        if confidence > best_confidence or (
            abs(confidence - best_confidence) < 1e-9 and score > best_score
        ):
            best_score = score
            best_confidence = confidence
            best = odds

    if best is None:
        return None

    # Return a copy with match_confidence set (avoids mutating the shared cache object)
    return dataclasses.replace(best, match_confidence=best_confidence)
