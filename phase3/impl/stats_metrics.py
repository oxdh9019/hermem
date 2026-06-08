"""Hermem stats metrics — V6 Sprint 0.

Pure functions, no hidden state (except the module-level L2 merge counter),
consumed by the hermes hermem stats CLI and unit-tested in hermem impl repo.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional


def compute_avg_inject_token(log_path: Path, days: int = 7) -> float | None:
    """Read hermem_inject_log.jsonl and return average token estimate within N days.

    Returns None if file missing, empty, or all entries are out of window.
    """
    if not log_path.exists():
        return None
    cutoff = datetime.now(UTC).timestamp() - days * 86400
    total = 0
    count = 0
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts_str = rec.get("ts", "")
                if not ts_str:
                    continue
                # Accept "Z" suffix (ISO 8601 UTC shorthand)
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts.timestamp() < cutoff:
                    continue
                total += int(rec.get("token_est", 0))
                count += 1
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                # Skip corrupted lines
                continue
    if count == 0:
        return None
    return round(total / count, 1)


def compute_dedup_rate(conn, days: int = 7, outcome_col: str = "outcome") -> float | None:
    """Return dedup/merge rate of l1_dispositions over the last N days.

    Returns None if the table or outcome column does not exist (V5.5 schema
    not yet augmented), or if no rows fall inside the window.
    """
    try:
        # Table existence
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='l1_dispositions'"
            ).fetchall()
        ]
        if not tables:
            return None
        # Column existence
        cols = [r[1] for r in conn.execute("PRAGMA table_info(l1_dispositions)").fetchall()]
        if outcome_col not in cols:
            return None
        # NB: l1_dispositions.created_at is stored as ISO 8601 TEXT (NOT julianday),
        # so window math must use datetime() on the RHS — not julianday().
        # Contrast with chunks.created_at which IS julianday REAL.
        row = conn.execute(
            f"""SELECT
                   COUNT(*) AS total,
                   SUM(CASE WHEN {outcome_col} IN ('duplicate', 'merged') THEN 1 ELSE 0 END) AS dedup
               FROM l1_dispositions
               WHERE created_at > datetime('now', ?)""",
            (f"-{days} days",),
        ).fetchone()
        total, dedup = (row[0] or 0), (row[1] or 0)
        if total == 0:
            return None
        return round(dedup / total, 4)
    except Exception:
        return None


def compute_hit_rate(conn, days: int = 30) -> float | None:
    """Return fraction of chunks recalled or created within the last N days.

    NB: chunks.last_used_at and chunks.created_at are stored as Julian Day
    floats (REAL with DEFAULT julianday('now')). Comparing them to
    datetime('now', ...) — which returns an ISO 8601 string — would silently
    return zero hits. This was a Sprint 0 regression we want to lock down.
    Always use julianday() for window math against these columns.
    """
    row = conn.execute(
        """SELECT
               COUNT(*) AS total,
               SUM(CASE WHEN last_used_at > julianday('now', ?)
                         OR created_at > julianday('now', ?) THEN 1 ELSE 0 END) AS hit
           FROM chunks""",
        (f"-{days} days", f"-{days} days"),
    ).fetchone()
    total, hit = (row[0] or 0), (row[1] or 0)
    if total == 0:
        return None
    return round(hit / total, 4)


# ── L2 merge counter (Sprint 0 task 0.5) ───────────────────────────────────

_merge_lock = threading.Lock()
_merge_counter = {"count": 0, "date": ""}


def record_merge_attempt() -> None:
    """Increment the L2 scene-merge counter (thread-safe, daily reset)."""
    with _merge_lock:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if _merge_counter["date"] != today:
            _merge_counter["date"] = today
            _merge_counter["count"] = 0
        _merge_counter["count"] += 1


def get_merge_counter() -> dict:
    """Return today's L2 merge trigger count (consumed by stats CLI)."""
    with _merge_lock:
        # Reset on read if date rolled over (avoids stale state)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if _merge_counter["date"] != today:
            return {"count": 0, "date": today}
        return dict(_merge_counter)


def reset_merge_counter_for_testing() -> None:
    """Reset the module-level counter. Tests only."""
    with _merge_lock:
        _merge_counter["count"] = 0
        _merge_counter["date"] = ""
