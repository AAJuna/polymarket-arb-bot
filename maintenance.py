"""
Maintenance helpers for one-shot cleanup and fresh starts.
Only touches known bot-owned files inside data/ and logs/.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("data")
LOGS_DIR = Path("logs")
RESET_BACKUPS_DIR = Path("backups") / "runtime-resets"

STATE_FILES = (
    DATA_DIR / "portfolio.json",
    DATA_DIR / "portfolio.json.bak",
    DATA_DIR / "ai_stats.json",
    DATA_DIR / "risk_events.jsonl",
    DATA_DIR / "shadow_signals.json",
    DATA_DIR / "shadow_report.json",
    DATA_DIR / "strategy_expectancy.json",
    DATA_DIR / "trade_ledger.jsonl",
    DATA_DIR / "realtime_feed_status.json",
)

LOG_PATTERNS = (
    "bot.log*",
    "trades.log*",
)


def _remove_file(path: Path) -> tuple[str, str]:
    """Return (status, detail) where status is removed/missing/error."""
    try:
        if not path.exists():
            return "missing", str(path)
        path.unlink()
        return "removed", str(path)
    except Exception as exc:
        return "error", f"{path}: {exc}"


def _next_reset_backup_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidate = RESET_BACKUPS_DIR / stamp
    suffix = 1
    while candidate.exists():
        candidate = RESET_BACKUPS_DIR / f"{stamp}_{suffix:02d}"
        suffix += 1
    return candidate


def _snapshot_runtime_state(targets: list[Path]) -> dict:
    existing = [path for path in targets if path.exists()]
    if not existing:
        return {"path": None, "copied": [], "errors": []}

    backup_dir = _next_reset_backup_dir()
    copied: list[str] = []
    errors: list[str] = []

    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
    except Exception as exc:
        return {"path": None, "copied": [], "errors": [f"{backup_dir}: {exc}"]}

    for path in existing:
        dest = backup_dir / path
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            copied.append(str(dest))
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_paths": [str(path) for path in existing],
        "copied_paths": copied,
        "copy_errors": errors,
    }
    try:
        with open(backup_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
    except Exception as exc:
        errors.append(f"{backup_dir / 'manifest.json'}: {exc}")

    return {
        "path": str(backup_dir),
        "copied": copied,
        "errors": errors,
    }


def reset_runtime_state(clear_logs: bool = True) -> dict:
    """Delete persisted bot state so the next run starts fresh."""
    removed: list[str] = []
    missing: list[str] = []
    errors: list[str] = []

    targets = list(STATE_FILES)
    if clear_logs:
        for pattern in LOG_PATTERNS:
            targets.extend(sorted(LOGS_DIR.glob(pattern)))

    snapshot = _snapshot_runtime_state(targets)

    for path in targets:
        status, detail = _remove_file(path)
        if status == "removed":
            removed.append(detail)
        elif status == "missing":
            missing.append(detail)
        else:
            errors.append(detail)

    return {
        "removed": removed,
        "missing": missing,
        "errors": errors,
        "clear_logs": clear_logs,
        "snapshot": snapshot,
    }
