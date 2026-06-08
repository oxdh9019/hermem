"""Hermem stats metrics — V6 Sprint 0.

纯函数,无状态,供 hermes hermem stats CLI 调用,可在 hermem impl 仓库独立测试。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Optional


def compute_avg_inject_token(log_path: Path, days: int = 7) -> float | None:
    """读取 hermem_inject_log.jsonl,返回 N 天窗口内平均 token 估算。

    Args:
        log_path: jsonl 日志文件路径
        days: 窗口天数(默认 7)

    Returns:
        float: 平均 token 估算;None 表示文件不存在 / 无窗口内记录 / 全无效
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
                # 兼容 "...Z" 结尾(ISO 8601 UTC)
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts.timestamp() < cutoff:
                    continue
                total += int(rec.get("token_est", 0))
                count += 1
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                # 单行损坏不影响整体 — 跳过
                continue
    if count == 0:
        return None
    return round(total / count, 1)


def compute_dedup_rate(conn, days: int = 7, outcome_col: str = "outcome") -> float | None:
    """返回 V5.5 l1_dispositions 表的 dedup/merge 比例。

    Args:
        conn: SQLite connection(get_db() 上下文)
        days: 窗口天数
        outcome_col: outcome 字段名(Sprint 0 阶段可能不存在,提前检测)

    Returns:
        float: dedup 比例(0-1);None 表示字段缺失 / 0 行 / 表不存在
    """
    try:
        # 表存在性
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='l1_dispositions'"
            ).fetchall()
        ]
        if not tables:
            return None
        # 字段存在性
        cols = [r[1] for r in conn.execute("PRAGMA table_info(l1_dispositions)").fetchall()]
        if outcome_col not in cols:
            return None
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


# ── L2 merge counter(Sprint 0 任务 0.5)────────────────────────────────

import threading as _threading

_merge_lock = _threading.Lock()
_merge_counter = {"count": 0, "date": ""}


def record_merge_attempt() -> None:
    """L2 scene merge 路径触发时调用(线程安全,每日重置)。"""
    with _merge_lock:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if _merge_counter["date"] != today:
            _merge_counter["date"] = today
            _merge_counter["count"] = 0
        _merge_counter["count"] += 1


def get_merge_counter() -> dict:
    """返回当日 L2 merge 触发次数(供 stats CLI 调用)。"""
    with _merge_lock:
        # 读取时也重置(防止跨日遗留)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if _merge_counter["date"] != today:
            return {"count": 0, "date": today}
        return dict(_merge_counter)


def reset_merge_counter_for_testing() -> None:
    """仅供单元测试使用 — 重置模块级 counter。"""
    with _merge_lock:
        _merge_counter["count"] = 0
        _merge_counter["date"] = ""
