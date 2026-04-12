"""
Advanced football match analytics used to enrich AI validation prompts.

The model is optional and provider-backed when Sportmonks is configured.
If the provider is unavailable, it falls back to sportsbook-only probabilities
so the bot can continue running with a lower-confidence baseline.
"""

from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

import config
import data_feeds
from data_feeds import ExternalOdds
from logger_setup import get_logger
from utils import TTLCache, parse_iso, retry, utcnow

logger = get_logger(__name__)

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})
_analysis_cache = TTLCache(ttl_seconds=config.MATCH_ANALYTICS_CACHE_TTL)
_team_cache = TTLCache(ttl_seconds=config.MATCH_ANALYTICS_CACHE_TTL)
_provider_usage_lock = threading.Lock()
_PROVIDER_USAGE_FILE = Path("data/match_provider_usage.json")

_SPORTMONKS_INCLUDE = (
    "participants;"
    "scores;"
    "statistics;"
    "statistics.type;"
    "lineups;"
    "sidelined;"
    "expectedLineups;"
    "xGFixture"
)

_STAT_SHOTS_ON_TARGET = 86
_STAT_SHOTS_TOTAL = 42
_STAT_GOALS = 52
_STAT_GOALS_CONCEDED = 88


class ProviderQuotaExceeded(RuntimeError):
    pass


def _provider_daily_limit(provider: str) -> int:
    if provider == "sportmonks":
        return max(0, config.SPORTMONKS_DAILY_LIMIT)
    if provider == "api_football":
        return max(0, config.API_FOOTBALL_DAILY_LIMIT)
    return 0


def _provider_day_key() -> str:
    return utcnow().date().isoformat()


def _load_provider_usage() -> dict[str, dict[str, int]]:
    if not _PROVIDER_USAGE_FILE.exists():
        return {}
    try:
        return json.loads(_PROVIDER_USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_provider_usage(payload: dict[str, dict[str, int]]) -> None:
    _PROVIDER_USAGE_FILE.parent.mkdir(exist_ok=True)
    _PROVIDER_USAGE_FILE.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _reserve_provider_call(provider: str) -> bool:
    limit = _provider_daily_limit(provider)
    if limit <= 0:
        return True

    with _provider_usage_lock:
        usage = _load_provider_usage()
        day_key = _provider_day_key()
        day_usage = usage.setdefault(day_key, {})
        calls = int(day_usage.get(provider, 0) or 0)
        if calls >= limit:
            return False
        day_usage[provider] = calls + 1

        for key in list(usage.keys()):
            if key < day_key:
                usage.pop(key, None)
        _save_provider_usage(usage)
    return True


def _provider_order() -> list[str]:
    ordered: list[str] = []
    for provider in config.MATCH_DATA_PROVIDERS:
        if provider in {"sportmonks", "api_football", "sportsbook_only"} and provider not in ordered:
            ordered.append(provider)
    if "sportsbook_only" not in ordered:
        ordered.append("sportsbook_only")
    return ordered


def _normalize(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in _normalize(text).split() if len(token) > 1}


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _extract_number(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "score", "amount", "xg", "goals"):
            parsed = _extract_number(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except Exception:
            return None
    return None


@dataclass
class TeamStrength:
    team_id: Optional[int]
    team_name: str
    sample_size: int
    weighted_goals_for: float
    weighted_goals_against: float
    weighted_shots_on_target_for: float
    weighted_shots_on_target_against: float
    weighted_xg_for: float
    weighted_xg_against: float
    win_rate: float
    focus: str
    unavailable_count: int = 0
    lineup_known: bool = False
    attack_rating: float = 0.0
    defense_rating: float = 0.0

    def summary(self) -> str:
        lineup_bits = []
        if self.lineup_known:
            lineup_bits.append(f"absences={self.unavailable_count}")
        return (
            f"{self.team_name}: n={self.sample_size}, focus={self.focus}, "
            f"GF={self.weighted_goals_for:.2f}, GA={self.weighted_goals_against:.2f}, "
            f"SOT={self.weighted_shots_on_target_for:.2f}/{self.weighted_shots_on_target_against:.2f}, "
            f"xG={self.weighted_xg_for:.2f}/{self.weighted_xg_against:.2f}, "
            f"win_rate={self.win_rate:.1%}"
            + (f", {' '.join(lineup_bits)}" if lineup_bits else "")
        )


@dataclass
class MatchupAnalysis:
    provider: str
    home_team: str
    away_team: str
    yes_side: str
    model_confidence: float
    sportsbook_home_prob: float
    sportsbook_away_prob: float
    sportsbook_draw_prob: Optional[float]
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    yes_true_prob: float
    no_true_prob: float
    expected_goals_home: float
    expected_goals_away: float
    lookback_matches: int
    head_to_head_matches: int
    home_strength: Optional[TeamStrength] = None
    away_strength: Optional[TeamStrength] = None
    notes: list[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        lines = [
            f"Advanced match model ({self.provider}, confidence={self.model_confidence:.2f})",
            f"- Simulated result probabilities: home={self.home_win_prob:.1%} draw={self.draw_prob:.1%} away={self.away_win_prob:.1%}",
            f"- Fair market probabilities: YES={self.yes_true_prob:.1%} NO={self.no_true_prob:.1%}",
            f"- Expected goals: home={self.expected_goals_home:.2f} away={self.expected_goals_away:.2f}",
            (
                f"- Sportsbook baseline: home={self.sportsbook_home_prob:.1%} "
                f"draw={(self.sportsbook_draw_prob or 0.0):.1%} "
                f"away={self.sportsbook_away_prob:.1%}"
            ),
            f"- Samples: recent={self.lookback_matches} per team, h2h={self.head_to_head_matches}",
        ]
        if self.home_strength is not None:
            lines.append(f"- Home form: {self.home_strength.summary()}")
        if self.away_strength is not None:
            lines.append(f"- Away form: {self.away_strength.summary()}")
        if self.notes:
            lines.append(f"- Notes: {'; '.join(self.notes[:4])}")
        return "\n".join(lines)


@dataclass
class _FixtureSample:
    date_key: str
    team_id: int
    opponent_id: Optional[int]
    is_home: bool
    goals_for: float
    goals_against: float
    shots_on_target_for: float
    shots_on_target_against: float
    xg_for: Optional[float]
    xg_against: Optional[float]
    points: float


def _sportmonks_enabled() -> bool:
    return bool(config.MATCH_ANALYTICS_ENABLED and config.SPORTMONKS_API_KEY)


def _api_football_enabled() -> bool:
    return bool(config.MATCH_ANALYTICS_ENABLED and config.API_FOOTBALL_API_KEY)


def _sportmonks_payload(response: requests.Response) -> list[dict]:
    data = response.json()
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            return rows
        if isinstance(rows, dict):
            return [rows]
    if isinstance(data, list):
        return data
    return []


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _sportmonks_get(path: str, **params) -> list[dict]:
    url = f"{config.SPORTMONKS_API_BASE.rstrip('/')}/{path.lstrip('/')}"
    query = {
        "api_token": config.SPORTMONKS_API_KEY,
        "per_page": 50,
        **{key: value for key, value in params.items() if value is not None},
    }

    results: list[dict] = []
    page = 1
    while True:
        if not _reserve_provider_call("sportmonks"):
            raise ProviderQuotaExceeded("sportmonks daily limit reached")
        resp = _session.get(url, params={**query, "page": page}, timeout=12)
        if resp.status_code in (402, 403, 429):
            raise ProviderQuotaExceeded(f"sportmonks quota/status={resp.status_code}")
        resp.raise_for_status()
        rows = _sportmonks_payload(resp)
        results.extend(rows)
        payload = resp.json() if resp.content else {}
        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        if not isinstance(pagination, dict) or not pagination.get("has_more"):
            break
        page += 1
    return results


def _team_match_score(query: str, candidate: dict) -> float:
    query_tokens = _tokens(query)
    name = str(candidate.get("name", "") or "")
    name_tokens = _tokens(name)
    if not name_tokens:
        return 0.0
    exact = 1.0 if _normalize(name) == _normalize(query) else 0.0
    overlap = len(query_tokens & name_tokens) / max(1, len(name_tokens))
    return exact * 2.0 + overlap


def _search_team_sportmonks(name: str) -> Optional[dict]:
    cache_key = f"sportmonks:team:{_normalize(name)}"
    cached = _team_cache.get(cache_key)
    if cached is not None:
        return cached
    if not _sportmonks_enabled():
        return None

    try:
        rows = _sportmonks_get(
            f"teams/search/{quote(name)}",
            include="latest;sidelined",
            order="desc",
        )
    except Exception as exc:
        logger.debug(f"Team search failed for {name}: {exc}")
        _team_cache.set(cache_key, None)
        return None

    best = None
    best_score = 0.0
    for row in rows:
        score = _team_match_score(name, row)
        if score > best_score:
            best = row
            best_score = score

    _team_cache.set(cache_key, best)
    return best


def _api_football_payload(response: requests.Response) -> list[dict]:
    data = response.json()
    if isinstance(data, dict):
        rows = data.get("response")
        if isinstance(rows, list):
            return rows
    return []


@retry(max_attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
def _api_football_get(path: str, **params) -> list[dict]:
    if not _reserve_provider_call("api_football"):
        raise ProviderQuotaExceeded("api_football daily limit reached")

    if "from_" in params and "from" not in params:
        params["from"] = params.pop("from_")

    url = f"{config.API_FOOTBALL_API_BASE.rstrip('/')}/{path.lstrip('/')}"
    resp = _session.get(
        url,
        params={key: value for key, value in params.items() if value is not None},
        headers={
            "Accept": "application/json",
            "x-apisports-key": config.API_FOOTBALL_API_KEY,
        },
        timeout=12,
    )
    if resp.status_code in (402, 403, 429):
        raise ProviderQuotaExceeded(f"api_football quota/status={resp.status_code}")
    resp.raise_for_status()
    return _api_football_payload(resp)


def _search_team_api_football(name: str) -> Optional[dict]:
    cache_key = f"api_football:team:{_normalize(name)}"
    cached = _team_cache.get(cache_key)
    if cached is not None:
        return cached
    if not _api_football_enabled():
        return None

    try:
        rows = _api_football_get("teams", search=name)
    except Exception as exc:
        logger.debug(f"API-Football team search failed for {name}: {exc}")
        _team_cache.set(cache_key, None)
        return None

    best = None
    best_score = 0.0
    for row in rows:
        candidate = row.get("team") or {}
        score = _team_match_score(name, candidate)
        if score > best_score:
            best = {
                "id": candidate.get("id"),
                "name": candidate.get("name"),
            }
            best_score = score

    _team_cache.set(cache_key, best)
    return best


def _participant_map(fixture: dict) -> tuple[dict[int, dict], Optional[int], Optional[int]]:
    by_id: dict[int, dict] = {}
    home_id: Optional[int] = None
    away_id: Optional[int] = None
    participants = fixture.get("participants") or []
    for participant in participants:
        pid = _to_int(participant.get("id"))
        if pid is None:
            continue
        by_id[pid] = participant
        meta = participant.get("meta") or {}
        location = str(meta.get("location") or participant.get("location") or "").lower()
        if location == "home":
            home_id = pid
        elif location == "away":
            away_id = pid

    if home_id is None or away_id is None:
        ids = list(by_id.keys())
        if ids:
            home_id = home_id or ids[0]
        if len(ids) > 1:
            away_id = away_id or ids[1]
    return by_id, home_id, away_id


def _score_map(fixture: dict) -> dict[int, float]:
    current: dict[int, float] = {}
    fallback: dict[int, float] = {}
    for score in fixture.get("scores") or []:
        pid = _to_int(score.get("participant_id") or score.get("participantId"))
        value = _extract_number(score.get("score"))
        if value is None:
            value = _extract_number(score.get("data"))
        if value is None:
            value = _extract_number(score.get("value"))
        if pid is None or value is None:
            continue
        fallback[pid] = value
        description = str(score.get("description") or "").strip().lower()
        if description == "current":
            current[pid] = value
    return current or fallback


def _stat_code(stat: dict) -> tuple[Optional[int], str]:
    type_obj = stat.get("type") or {}
    type_id = _to_int(stat.get("type_id") or type_obj.get("id"))
    name = " ".join(
        str(type_obj.get(key, "") or "")
        for key in ("name", "code", "developer_name")
    ).lower()
    return type_id, name


def _stats_by_participant(fixture: dict) -> dict[int, dict[str, float]]:
    stats: dict[int, dict[str, float]] = {}
    for item in fixture.get("statistics") or []:
        participant_id = _to_int(item.get("participant_id") or item.get("participantId"))
        value = _extract_number(item.get("data"))
        if value is None:
            value = _extract_number(item.get("value"))
        if participant_id is None or value is None:
            continue
        type_id, name = _stat_code(item)
        bucket = stats.setdefault(participant_id, {})
        if type_id == _STAT_SHOTS_ON_TARGET or "shots on target" in name:
            bucket["shots_on_target"] = value
        elif type_id == _STAT_SHOTS_TOTAL or "shots total" in name:
            bucket["shots_total"] = value
        elif type_id == _STAT_GOALS or "goals" == name.strip():
            bucket["goals"] = value
        elif type_id == _STAT_GOALS_CONCEDED or "goals conceded" in name:
            bucket["goals_conceded"] = value
    return stats


def _xg_by_participant(fixture: dict) -> dict[int, float]:
    rows = (
        fixture.get("xGFixture")
        or fixture.get("xgFixture")
        or fixture.get("xg")
        or fixture.get("expected")
        or []
    )
    xg: dict[int, float] = {}
    if isinstance(rows, dict):
        rows = rows.get("data") or []
    for item in rows:
        participant_id = _to_int(item.get("participant_id") or item.get("participantId"))
        value = _extract_number(item.get("data"))
        if value is None:
            value = _extract_number(item.get("value"))
        if value is None:
            value = _extract_number(item.get("xg"))
        if participant_id is None or value is None:
            continue
        xg[participant_id] = value
    return xg


def _unavailable_count(payload: dict, team_id: int) -> tuple[int, bool]:
    sidelined = payload.get("sidelined") or []
    expected = payload.get("expectedLineups") or []
    count = 0
    known = False
    for item in sidelined:
        participant_id = _to_int(
            item.get("participant_id")
            or item.get("participantId")
            or item.get("team_id")
            or (item.get("participant") or {}).get("id")
            or (item.get("team") or {}).get("id")
        )
        if participant_id == team_id:
            count += 1
            known = True
    for item in expected:
        participant_id = _to_int(
            item.get("participant_id")
            or item.get("participantId")
            or item.get("team_id")
            or (item.get("participant") or {}).get("id")
            or (item.get("team") or {}).get("id")
        )
        if participant_id == team_id:
            known = True
            break
    return count, known


def _fixture_sample(fixture: dict, team_id: int) -> Optional[_FixtureSample]:
    by_id, home_id, away_id = _participant_map(fixture)
    if team_id not in by_id or home_id is None or away_id is None:
        return None

    score_map = _score_map(fixture)
    if home_id not in score_map or away_id not in score_map:
        return None

    stats_map = _stats_by_participant(fixture)
    xg_map = _xg_by_participant(fixture)

    starting_at = fixture.get("starting_at")
    try:
        dt = parse_iso(str(starting_at).replace(" ", "T"))
    except Exception:
        return None

    is_home = team_id == home_id
    opponent_id = away_id if is_home else home_id
    goals_for = score_map.get(team_id, 0.0)
    goals_against = score_map.get(opponent_id, 0.0)
    points = 3.0 if goals_for > goals_against else (1.0 if goals_for == goals_against else 0.0)

    team_stats = stats_map.get(team_id, {})
    opp_stats = stats_map.get(opponent_id, {})

    return _FixtureSample(
        date_key=dt.isoformat(),
        team_id=team_id,
        opponent_id=opponent_id,
        is_home=is_home,
        goals_for=goals_for,
        goals_against=goals_against,
        shots_on_target_for=float(team_stats.get("shots_on_target", 0.0)),
        shots_on_target_against=float(opp_stats.get("shots_on_target", 0.0)),
        xg_for=xg_map.get(team_id),
        xg_against=xg_map.get(opponent_id),
        points=points,
    )


def _weighted_average(values: list[float], weights: list[float]) -> float:
    if not values or not weights:
        return 0.0
    total_weight = sum(weights)
    if total_weight <= 0:
        return 0.0
    return sum(value * weight for value, weight in zip(values, weights)) / total_weight


def _build_strength(
    team: dict,
    fixtures: list[dict],
    focus_home: bool,
    upcoming_fixture: Optional[dict],
) -> Optional[TeamStrength]:
    team_id = _to_int(team.get("id"))
    team_name = str(team.get("name", "") or "")
    if team_id is None:
        return None

    samples: list[_FixtureSample] = []
    for fixture in fixtures:
        sample = _fixture_sample(fixture, team_id)
        if sample is None:
            continue
        samples.append(sample)

    if not samples:
        return None

    samples.sort(key=lambda sample: sample.date_key, reverse=True)
    location_filtered = [sample for sample in samples if sample.is_home == focus_home]
    chosen = location_filtered[: config.MATCH_LOOKBACK_MATCHES]
    focus = "home" if focus_home else "away"
    if len(chosen) < max(3, config.MATCH_LOOKBACK_MATCHES // 2):
        chosen = samples[: config.MATCH_LOOKBACK_MATCHES]
        focus = "overall"

    weights = [config.MATCH_RECENCY_DECAY ** idx for idx, _ in enumerate(chosen)]
    goals_for = [sample.goals_for for sample in chosen]
    goals_against = [sample.goals_against for sample in chosen]
    shots_for = [sample.shots_on_target_for for sample in chosen]
    shots_against = [sample.shots_on_target_against for sample in chosen]
    xg_for = [
        sample.xg_for
        if sample.xg_for is not None
        else (
            sample.shots_on_target_for * config.MATCH_XG_PER_SHOT_ON_TARGET
            if sample.shots_on_target_for > 0
            else sample.goals_for
        )
        for sample in chosen
    ]
    xg_against = [
        sample.xg_against
        if sample.xg_against is not None
        else (
            sample.shots_on_target_against * config.MATCH_XG_PER_SHOT_ON_TARGET
            if sample.shots_on_target_against > 0
            else sample.goals_against
        )
        for sample in chosen
    ]
    points = [sample.points / 3.0 for sample in chosen]

    unavailable_count = 0
    lineup_known = False
    if upcoming_fixture is not None:
        unavailable_count, lineup_known = _unavailable_count(upcoming_fixture, team_id)
    else:
        unavailable_count, lineup_known = _unavailable_count(team, team_id)

    weighted_xg_for = _weighted_average(xg_for, weights)
    weighted_xg_against = _weighted_average(xg_against, weights)
    weighted_goals_for = _weighted_average(goals_for, weights)
    weighted_goals_against = _weighted_average(goals_against, weights)
    weighted_shots_for = _weighted_average(shots_for, weights)
    weighted_shots_against = _weighted_average(shots_against, weights)
    availability_factor = max(0.85, 1.0 - unavailable_count * config.MATCH_LINEUP_ABSENCE_PENALTY)

    attack_rating = max(
        0.2,
        (0.60 * weighted_xg_for + 0.40 * weighted_goals_for)
        * availability_factor,
    )
    defense_rating = max(
        0.2,
        (0.60 * weighted_xg_against + 0.40 * weighted_goals_against)
        * (1.0 + unavailable_count * config.MATCH_LINEUP_ABSENCE_PENALTY * 0.5),
    )

    return TeamStrength(
        team_id=team_id,
        team_name=team_name,
        sample_size=len(chosen),
        weighted_goals_for=weighted_goals_for,
        weighted_goals_against=weighted_goals_against,
        weighted_shots_on_target_for=weighted_shots_for,
        weighted_shots_on_target_against=weighted_shots_against,
        weighted_xg_for=weighted_xg_for,
        weighted_xg_against=weighted_xg_against,
        win_rate=_weighted_average(points, weights),
        focus=focus,
        unavailable_count=unavailable_count,
        lineup_known=lineup_known,
        attack_rating=attack_rating,
        defense_rating=defense_rating,
    )


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _simulate_result_probs(home_lambda: float, away_lambda: float, max_goals: int = 8) -> tuple[float, float, float]:
    home_probs = [_poisson_pmf(goals, home_lambda) for goals in range(max_goals + 1)]
    away_probs = [_poisson_pmf(goals, away_lambda) for goals in range(max_goals + 1)]
    total = sum(home_probs) * sum(away_probs)
    if total <= 0:
        return 0.0, 0.0, 0.0

    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    for home_goals, hp in enumerate(home_probs):
        for away_goals, ap in enumerate(away_probs):
            prob = hp * ap
            if home_goals > away_goals:
                home_win += prob
            elif home_goals == away_goals:
                draw += prob
            else:
                away_win += prob

    scale = home_win + draw + away_win
    if scale <= 0:
        return 0.0, 0.0, 0.0
    return home_win / scale, draw / scale, away_win / scale


def _head_to_head_adjustment(home_team_id: int, fixtures: list[dict]) -> tuple[float, int, list[str]]:
    samples: list[_FixtureSample] = []
    for fixture in fixtures:
        sample = _fixture_sample(fixture, home_team_id)
        if sample is not None:
            samples.append(sample)
    if not samples:
        return 0.0, 0, []

    samples.sort(key=lambda sample: sample.date_key, reverse=True)
    recent = samples[:5]
    weights = [config.MATCH_RECENCY_DECAY ** idx for idx, _ in enumerate(recent)]
    goal_diff = _weighted_average(
        [sample.goals_for - sample.goals_against for sample in recent],
        weights,
    )
    point_edge = _weighted_average([sample.points / 3.0 for sample in recent], weights) - 0.5
    adjustment = max(
        -config.MATCH_HEAD_TO_HEAD_WEIGHT,
        min(config.MATCH_HEAD_TO_HEAD_WEIGHT, goal_diff * 0.03 + point_edge * 0.04),
    )
    notes = [f"h2h goal_diff={goal_diff:+.2f}", f"h2h point_edge={point_edge:+.2f}"]
    return adjustment, len(recent), notes


def _upcoming_fixture(home_team_id: int, away_team_id: int, fixtures: list[dict], kickoff) -> Optional[dict]:
    best = None
    best_gap = None
    for fixture in fixtures:
        by_id, home_id, away_id = _participant_map(fixture)
        if home_id != home_team_id or away_id != away_team_id:
            continue
        starting_at = fixture.get("starting_at")
        if not starting_at:
            continue
        try:
            dt = parse_iso(str(starting_at).replace(" ", "T"))
        except Exception:
            continue
        gap = abs((dt - kickoff).total_seconds())
        if best is None or gap < best_gap:
            best = fixture
            best_gap = gap
    return best


def _lightweight_analysis(ext: ExternalOdds, yes_side: str) -> MatchupAnalysis:
    home_prob = ext.home_prob
    away_prob = ext.away_prob
    draw_prob = ext.draw_prob or max(0.0, 1.0 - home_prob - away_prob)
    if yes_side == "home":
        yes_true_prob = home_prob
    elif yes_side == "away":
        yes_true_prob = away_prob
    else:
        yes_true_prob = draw_prob

    # Lower confidence for non-soccer sports where we have no advanced
    # team analytics — sportsbook odds alone are a weaker signal.
    sport = (ext.sport or "").lower()
    if "soccer" in sport:
        confidence = 0.38
        notes = ["advanced team feed unavailable; using sportsbook baseline only"]
    else:
        confidence = 0.15
        notes = [
            f"no advanced analytics for sport={ext.sport}; "
            "sportsbook-only baseline with reduced confidence"
        ]

    return MatchupAnalysis(
        provider="sportsbook_only",
        home_team=ext.home_team,
        away_team=ext.away_team,
        yes_side=yes_side,
        model_confidence=confidence,
        sportsbook_home_prob=home_prob,
        sportsbook_away_prob=away_prob,
        sportsbook_draw_prob=draw_prob,
        home_win_prob=home_prob,
        draw_prob=draw_prob,
        away_win_prob=away_prob,
        yes_true_prob=yes_true_prob,
        no_true_prob=max(0.0, 1.0 - yes_true_prob),
        expected_goals_home=max(0.2, home_prob * 2.2),
        expected_goals_away=max(0.2, away_prob * 2.2),
        lookback_matches=0,
        head_to_head_matches=0,
        notes=notes,
    )


def _build_matchup_from_strengths(
    provider: str,
    ext: ExternalOdds,
    yes_side: str,
    home_strength: TeamStrength,
    away_strength: TeamStrength,
    h2h_fixtures: list[dict],
    confidence_base: float,
    extra_notes: Optional[list[str]] = None,
) -> MatchupAnalysis:
    h2h_adjustment, h2h_count, h2h_notes = _head_to_head_adjustment(
        int(home_strength.team_id or 0),
        h2h_fixtures,
    )
    home_lambda = max(
        0.2,
        ((home_strength.attack_rating + away_strength.defense_rating) / 2.0)
        * (1.0 + config.MATCH_HOME_ADVANTAGE)
        * (1.0 + h2h_adjustment),
    )
    away_lambda = max(
        0.2,
        ((away_strength.attack_rating + home_strength.defense_rating) / 2.0)
        * (1.0 - h2h_adjustment),
    )
    home_lambda = min(home_lambda, 4.0)
    away_lambda = min(away_lambda, 4.0)

    home_win_prob, draw_prob, away_win_prob = _simulate_result_probs(home_lambda, away_lambda)
    if yes_side == "home":
        yes_true_prob = home_win_prob
    elif yes_side == "away":
        yes_true_prob = away_win_prob
    else:
        yes_true_prob = draw_prob

    confidence = min(
        0.92,
        confidence_base
        + min(home_strength.sample_size, config.MATCH_LOOKBACK_MATCHES) * 0.03
        + min(away_strength.sample_size, config.MATCH_LOOKBACK_MATCHES) * 0.03
        + min(h2h_count, 4) * 0.03
        + (0.05 if home_strength.lineup_known or away_strength.lineup_known else 0.0),
    )

    notes = [*h2h_notes]
    if home_strength.lineup_known or away_strength.lineup_known:
        notes.append(
            f"lineups home_abs={home_strength.unavailable_count} "
            f"away_abs={away_strength.unavailable_count}"
        )
    else:
        notes.append("lineups unavailable")
    if extra_notes:
        notes.extend(extra_notes)

    return MatchupAnalysis(
        provider=provider,
        home_team=home_strength.team_name,
        away_team=away_strength.team_name,
        yes_side=yes_side,
        model_confidence=confidence,
        sportsbook_home_prob=ext.home_prob,
        sportsbook_away_prob=ext.away_prob,
        sportsbook_draw_prob=ext.draw_prob,
        home_win_prob=home_win_prob,
        draw_prob=draw_prob,
        away_win_prob=away_win_prob,
        yes_true_prob=yes_true_prob,
        no_true_prob=max(0.0, 1.0 - yes_true_prob),
        expected_goals_home=home_lambda,
        expected_goals_away=away_lambda,
        lookback_matches=min(home_strength.sample_size, away_strength.sample_size),
        head_to_head_matches=h2h_count,
        home_strength=home_strength,
        away_strength=away_strength,
        notes=notes,
    )


def _sportmonks_analysis(ext: ExternalOdds, yes_side: str, end_date) -> Optional[MatchupAnalysis]:
    if "soccer" not in (ext.sport or "").lower():
        return None

    home_team = _search_team_sportmonks(ext.home_team)
    away_team = _search_team_sportmonks(ext.away_team)
    if home_team is None or away_team is None:
        return None

    home_team_id = _to_int(home_team.get("id"))
    away_team_id = _to_int(away_team.get("id"))
    if home_team_id is None or away_team_id is None:
        return None

    end_key = end_date.date().isoformat()
    start_key = (end_date.date() - timedelta(days=config.MATCH_LOOKBACK_DAYS)).isoformat()

    home_fixtures = _sportmonks_get(
        f"fixtures/between/{start_key}/{end_key}/{home_team_id}",
        include=_SPORTMONKS_INCLUDE,
        order="desc",
    )
    away_fixtures = _sportmonks_get(
        f"fixtures/between/{start_key}/{end_key}/{away_team_id}",
        include=_SPORTMONKS_INCLUDE,
        order="desc",
    )
    h2h_fixtures = _sportmonks_get(
        f"fixtures/head-to-head/{home_team_id}/{away_team_id}",
        include=_SPORTMONKS_INCLUDE,
        order="desc",
    )

    upcoming = _upcoming_fixture(home_team_id, away_team_id, home_fixtures + away_fixtures + h2h_fixtures, ext.commence_time)
    home_strength = _build_strength(home_team, home_fixtures, focus_home=True, upcoming_fixture=upcoming)
    away_strength = _build_strength(away_team, away_fixtures, focus_home=False, upcoming_fixture=upcoming)
    if home_strength is None or away_strength is None:
        return None

    return _build_matchup_from_strengths(
        provider="sportmonks",
        ext=ext,
        yes_side=yes_side,
        home_strength=home_strength,
        away_strength=away_strength,
        h2h_fixtures=h2h_fixtures,
        confidence_base=0.35,
    )


def _normalize_api_football_fixture(row: dict) -> Optional[dict]:
    fixture = row.get("fixture") or {}
    teams = row.get("teams") or {}
    goals = row.get("goals") or {}

    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_id = _to_int(home.get("id"))
    away_id = _to_int(away.get("id"))
    if home_id is None or away_id is None:
        return None

    normalized = {
        "id": _to_int(fixture.get("id")),
        "starting_at": fixture.get("date"),
        "participants": [
            {
                "id": home_id,
                "name": home.get("name"),
                "meta": {"location": "home"},
            },
            {
                "id": away_id,
                "name": away.get("name"),
                "meta": {"location": "away"},
            },
        ],
        "scores": [],
        "statistics": [],
        "sidelined": [],
        "expectedLineups": [],
    }

    home_goals = _extract_number(goals.get("home"))
    away_goals = _extract_number(goals.get("away"))
    if home_goals is not None and away_goals is not None:
        normalized["scores"] = [
            {"participant_id": home_id, "score": home_goals, "description": "current"},
            {"participant_id": away_id, "score": away_goals, "description": "current"},
        ]
    return normalized


def _api_football_recent_fixtures(team_id: int, start_key: str, end_key: str) -> list[dict]:
    rows = _api_football_get(
        "fixtures",
        team=team_id,
        from_=start_key,
        to=end_key,
        status="FT-AET-PEN",
    )
    normalized: list[dict] = []
    for row in rows:
        item = _normalize_api_football_fixture(row)
        if item is not None:
            normalized.append(item)
    return normalized


def _api_football_head_to_head(home_team_id: int, away_team_id: int) -> list[dict]:
    rows = _api_football_get(
        "fixtures/headtohead",
        h2h=f"{home_team_id}-{away_team_id}",
        last=5,
        status="FT-AET-PEN",
    )
    normalized: list[dict] = []
    for row in rows:
        item = _normalize_api_football_fixture(row)
        if item is not None:
            normalized.append(item)
    return normalized


def _api_football_analysis(ext: ExternalOdds, yes_side: str, end_date) -> Optional[MatchupAnalysis]:
    if "soccer" not in (ext.sport or "").lower():
        return None

    home_team = _search_team_api_football(ext.home_team)
    away_team = _search_team_api_football(ext.away_team)
    if home_team is None or away_team is None:
        return None

    home_team_id = _to_int(home_team.get("id"))
    away_team_id = _to_int(away_team.get("id"))
    if home_team_id is None or away_team_id is None:
        return None

    end_key = end_date.date().isoformat()
    start_key = (end_date.date() - timedelta(days=config.MATCH_LOOKBACK_DAYS)).isoformat()

    home_fixtures = _api_football_recent_fixtures(home_team_id, start_key, end_key)
    away_fixtures = _api_football_recent_fixtures(away_team_id, start_key, end_key)
    h2h_fixtures = _api_football_head_to_head(home_team_id, away_team_id)

    home_strength = _build_strength(home_team, home_fixtures, focus_home=True, upcoming_fixture=None)
    away_strength = _build_strength(away_team, away_fixtures, focus_home=False, upcoming_fixture=None)
    if home_strength is None or away_strength is None:
        return None

    return _build_matchup_from_strengths(
        provider="api_football",
        ext=ext,
        yes_side=yes_side,
        home_strength=home_strength,
        away_strength=away_strength,
        h2h_fixtures=h2h_fixtures,
        confidence_base=0.26,
        extra_notes=[
            "api-football fallback model uses goals-based form only",
            "shots on target, xG, and lineups were not fetched from fallback provider",
        ],
    )


def get_matchup_analysis_for_market(
    question: str,
    end_date,
    context_text: str = "",
    odds: Optional[ExternalOdds] = None,
) -> Optional[MatchupAnalysis]:
    ext = odds or data_feeds.get_odds_for_market(question, end_date, context_text=context_text)
    if ext is None:
        return None

    yes_side = data_feeds.match_team_side(question, ext)
    if yes_side is None:
        return None

    cache_key = f"{ext.event_key}:{yes_side}:{'|'.join(_provider_order())}"
    cached = _analysis_cache.get(cache_key)
    if cached is not None:
        return cached

    analysis: Optional[MatchupAnalysis] = None
    for provider in _provider_order():
        if provider == "sportmonks":
            if not _sportmonks_enabled():
                continue
            try:
                analysis = _sportmonks_analysis(ext, yes_side, end_date)
            except ProviderQuotaExceeded as exc:
                logger.info(f"Match analytics provider skipped ({provider}): {exc}")
                continue
            except Exception as exc:
                logger.debug(f"Sportmonks matchup analysis failed for {ext.event_key}: {exc}")
                continue
        elif provider == "api_football":
            if not _api_football_enabled():
                continue
            try:
                analysis = _api_football_analysis(ext, yes_side, end_date)
            except ProviderQuotaExceeded as exc:
                logger.info(f"Match analytics provider skipped ({provider}): {exc}")
                continue
            except Exception as exc:
                logger.debug(f"API-Football matchup analysis failed for {ext.event_key}: {exc}")
                continue
        elif provider == "sportsbook_only":
            analysis = _lightweight_analysis(ext, yes_side)

        if analysis is not None:
            break

    if analysis is None:
        analysis = _lightweight_analysis(ext, yes_side)

    _analysis_cache.set(cache_key, analysis)
    return analysis


def get_matchup_analysis_for_opportunity(opp) -> Optional[MatchupAnalysis]:
    if getattr(opp, "type", "") != "odds_comparison":
        return None
    context = " ".join(
        value
        for value in (
            getattr(opp, "event_slug", ""),
            getattr(opp, "market_slug", ""),
            getattr(opp, "slug", ""),
        )
        if value
    )
    return get_matchup_analysis_for_market(
        question=opp.question,
        end_date=opp.end_date,
        context_text=context,
        odds=getattr(opp, "external_odds", None),
    )
