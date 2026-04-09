"""
Append-only risk/decision journal for paper and live runs.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from logger_setup import get_logger
from utils import TTLCache, utcnow

logger = get_logger(__name__)

DATA_DIR = Path("data")
RISK_EVENTS_FILE = DATA_DIR / "risk_events.jsonl"
RISK_EVENT_DEDUPE_SECONDS = 300


class RiskEventJournal:
    def __init__(self):
        self._lock = threading.Lock()
        self._recent = TTLCache(ttl_seconds=RISK_EVENT_DEDUPE_SECONDS)
        DATA_DIR.mkdir(exist_ok=True)

    def record(
        self,
        *,
        cycle: int,
        stage: str,
        event: str,
        reason: str,
        opp=None,
        extra: Optional[dict[str, Any]] = None,
        dedupe_key: str = "",
    ) -> bool:
        payload: dict[str, Any] = {
            "timestamp": utcnow().isoformat(),
            "cycle": cycle,
            "stage": stage,
            "event": event,
            "reason": reason,
        }

        if opp is not None:
            payload.update({
                "market_id": str(getattr(opp, "market_id", "") or ""),
                "condition_id": str(getattr(opp, "condition_id", "") or ""),
                "token_id": str(getattr(opp, "token_id", "") or ""),
                "question": str(getattr(opp, "question", "") or ""),
                "side": str(getattr(opp, "side", "") or ""),
                "price": float(getattr(opp, "price", 0.0) or 0.0),
                "edge_pct": float(getattr(opp, "edge_pct", 0.0) or 0.0),
                "strategy_type": str(getattr(opp, "type", "") or ""),
                "market_url": str(getattr(opp, "market_url", "") or ""),
            })
            ext = getattr(opp, "external_odds", None)
            if ext is not None and getattr(ext, "match_confidence", None) is not None:
                payload["match_confidence"] = float(ext.match_confidence)

        if extra:
            payload["extra"] = extra

        event_key = dedupe_key or self._default_key(stage, event, reason, payload)
        if event_key and self._recent.get(event_key) is not None:
            return False
        if event_key:
            self._recent.set(event_key, True)

        self._append(payload)
        return True

    @staticmethod
    def _default_key(stage: str, event: str, reason: str, payload: dict[str, Any]) -> str:
        return ":".join([
            stage,
            event,
            reason,
            str(payload.get("market_id", "") or ""),
            str(payload.get("token_id", "") or ""),
        ])

    def _append(self, payload: dict[str, Any]) -> None:
        try:
            DATA_DIR.mkdir(exist_ok=True)
            with self._lock:
                with open(RISK_EVENTS_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, default=str) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
        except Exception as exc:
            logger.error(f"Failed to append risk event journal: {exc}")
