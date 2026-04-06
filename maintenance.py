"""
Maintenance helpers for one-shot cleanup and fresh starts.
Only touches known bot-owned files inside data/ and logs/.
"""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path("data")
LOGS_DIR = Path("logs")

STATE_FILES = (
    DATA_DIR / "portfolio.json",
    DATA_DIR / "ai_stats.json",
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


def reset_runtime_state(clear_logs: bool = True) -> dict:
    """Delete persisted bot state so the next run starts fresh."""
    removed: list[str] = []
    missing: list[str] = []
    errors: list[str] = []

    targets = list(STATE_FILES)
    if clear_logs:
        for pattern in LOG_PATTERNS:
            targets.extend(sorted(LOGS_DIR.glob(pattern)))

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
    }
