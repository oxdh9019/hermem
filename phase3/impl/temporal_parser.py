"""Hermem V6 Sprint 1 任务 1.5 — Temporal 检索通道。

5-7 条中文 regex 解析相对时间词("上周"/"上个月"/"昨天"/"Q1 2026"等)为
(start, end) 时间区间,供 hermem_search(time_range=...) 过滤。

决策 6:用 regex 而非 dateparser 库(节省 200KB 依赖,中文用例够用)。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Optional

# 决策 6:5-7 条中文 regex + 数字格式
# 顺序:从最具体到最宽松(优先级 — 第一个匹配生效)
_TEMPORAL_PATTERNS = [
    # 1. Q1 2026 / Q4 2025 季度格式
    (
        re.compile(r"[Qq]([1-4])\s*(\d{4})"),
        lambda m, now: _quarter_range(int(m.group(1)), int(m.group(2)), now),
    ),
    # 2. YYYY-MM 月份格式
    (
        re.compile(r"(\d{4})-(\d{1,2})\b"),
        lambda m, now: _month_range(int(m.group(1)), int(m.group(2)), now),
    ),
    # 3. N 天前
    (
        re.compile(r"(\d+)\s*天前"),
        lambda m, now: _days_ago_range(int(m.group(1)), now),
    ),
    # 4. N 周前
    (
        re.compile(r"(\d+)\s*周前"),
        lambda m, now: _weeks_ago_range(int(m.group(1)), now),
    ),
    # 5. 上周
    (
        re.compile(r"上周"),
        lambda m, now: _last_week_range(now),
    ),
    # 6. 上个月 / 上月
    (
        re.compile(r"上(个)?月"),
        lambda m, now: _last_month_range(now),
    ),
    # 7. 昨天 / 前天 / 今天
    (
        re.compile(r"前天"),
        lambda m, now: _day_range(now, -2),
    ),
    (
        re.compile(r"昨天"),
        lambda m, now: _day_range(now, -1),
    ),
    (
        re.compile(r"今天"),
        lambda m, now: _day_range(now, 0),
    ),
]


def parse_relative_time(
    text: str | None,
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """从用户消息提取 (start, end) 时间区间。

    Args:
        text: 用户消息(可能含时间词)
        now: 当前时间(测试用;默认 UTC now)

    Returns:
        (start, end) 元组(UTC);None 表示无时间词命中

    Examples:
        "上周做了 X" → (上周一 00:00, 本周一 00:00)
        "2026-05 的 session" → (2026-05-01, 2026-06-01)
        "3 天前讨论的" → (3 天前 00:00, 2 天前 00:00)
    """
    if not text:
        return None
    if now is None:
        now = datetime.now(UTC)

    for pattern, handler in _TEMPORAL_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                return handler(m, now)
            except (ValueError, IndexError):
                continue
    return None


# ── Helper:区间计算 ──────────────────────────────────────────────────────


def _quarter_range(q: int, year: int, now: datetime) -> tuple[datetime, datetime]:
    """Q1=1-3月, Q2=4-6月, Q3=7-9月, Q4=10-12月。"""
    if not (1 <= q <= 4):
        raise ValueError(f"invalid quarter: {q}")
    start_month = (q - 1) * 3 + 1
    end_month = start_month + 3
    if end_month > 12:
        return (datetime(year, start_month, 1, tzinfo=UTC), datetime(year + 1, 1, 1, tzinfo=UTC))
    return (datetime(year, start_month, 1, tzinfo=UTC), datetime(year, end_month, 1, tzinfo=UTC))


def _month_range(year: int, month: int, now: datetime) -> tuple[datetime, datetime]:
    """YYYY-MM 格式。"""
    if not (1 <= month <= 12):
        raise ValueError(f"invalid month: {month}")
    if month == 12:
        return (datetime(year, 12, 1, tzinfo=UTC), datetime(year + 1, 1, 1, tzinfo=UTC))
    return (datetime(year, month, 1, tzinfo=UTC), datetime(year, month + 1, 1, tzinfo=UTC))


def _days_ago_range(n: int, now: datetime) -> tuple[datetime, datetime]:
    """N 天前的当天。"""
    target = now - timedelta(days=n)
    start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (start, end)


def _weeks_ago_range(n: int, now: datetime) -> tuple[datetime, datetime]:
    """N 周前(整周)。"""
    target = now - timedelta(weeks=n)
    start = target - timedelta(days=target.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return (start, start + timedelta(weeks=1))


def _last_week_range(now: datetime) -> tuple[datetime, datetime]:
    """上周一 00:00 到本周一 00:00。"""
    this_monday = now - timedelta(days=now.weekday())
    this_monday = this_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return (this_monday - timedelta(weeks=1), this_monday)


def _last_month_range(now: datetime) -> tuple[datetime, datetime]:
    """上月 1 号到本月 1 号。"""
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if this_month_start.month == 1:
        last_month_start = datetime(this_month_start.year - 1, 12, 1, tzinfo=UTC)
    else:
        last_month_start = datetime(
            this_month_start.year, this_month_start.month - 1, 1, tzinfo=UTC
        )
    return (last_month_start, this_month_start)


def _day_range(now: datetime, offset_days: int) -> tuple[datetime, datetime]:
    """当天 00:00 到次日 00:00。offset_days = -1 (昨天) / 0 (今天) / -2 (前天)。"""
    target = now + timedelta(days=offset_days)
    start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    return (start, start + timedelta(days=1))
