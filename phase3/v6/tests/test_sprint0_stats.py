"""V6 Sprint 0 单元测试 — stats 指标计算。

测试 impl.stats_metrics 的 4 个纯函数:
- compute_avg_inject_token: jsonl 日志 avg
- compute_dedup_rate: V5.5 disposition 字段缺失/表缺失降级
- compute_hit_rate: 关键回归 — Julian Day 浮点 vs datetime 字符串
- get_merge_counter: 每日重置 + 线程安全

不依赖 cli.py 本身(避免 sys.path 注入问题),直接测纯函数。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

# ── compute_avg_inject_token ────────────────────────────────────────────────


def test_avg_inject_token_returns_none_when_file_missing(tmp_path: Path):
    """日志文件不存在 → 返回 None(不报错)。"""
    from impl.stats_metrics import compute_avg_inject_token

    assert compute_avg_inject_token(tmp_path / "missing.jsonl") is None


def test_avg_inject_token_empty_file(tmp_path: Path):
    """空文件 → 返回 None。"""
    from impl.stats_metrics import compute_avg_inject_token

    log = tmp_path / "empty.jsonl"
    log.write_text("")
    assert compute_avg_inject_token(log) is None


def test_avg_inject_token_basic_avg(tmp_path: Path):
    """3 条 7 天内日志(100/200/300) → avg=200,1 条超出 7 天忽略。"""
    from impl.stats_metrics import compute_avg_inject_token

    log = tmp_path / "test.jsonl"
    now = datetime.now(UTC)
    log.write_text(
        "\n".join(
            [
                json.dumps({"ts": (now - timedelta(days=0)).isoformat(), "token_est": 100}),
                json.dumps({"ts": (now - timedelta(days=3)).isoformat(), "token_est": 200}),
                json.dumps({"ts": (now - timedelta(days=6)).isoformat(), "token_est": 300}),
                json.dumps(
                    {"ts": (now - timedelta(days=30)).isoformat(), "token_est": 999}
                ),  # 超出
            ]
        )
    )
    avg = compute_avg_inject_token(log, days=7)
    assert avg == 200.0  # (100+200+300)/3


def test_avg_inject_token_handles_z_suffix(tmp_path: Path):
    """兼容 ISO 8601 'Z' 后缀(UTC 缩写)。"""
    from impl.stats_metrics import compute_avg_inject_token

    log = tmp_path / "test.jsonl"
    log.write_text(
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "token_est": 50,
            }
        )
    )
    assert compute_avg_inject_token(log) == 50.0


def test_avg_inject_token_skips_corrupted_lines(tmp_path: Path):
    """损坏行不阻断整体,只跳该行。"""
    from impl.stats_metrics import compute_avg_inject_token

    log = tmp_path / "test.jsonl"
    now = datetime.now(UTC)
    log.write_text(
        "\n".join(
            [
                "这不是 JSON,直接跳过",
                json.dumps({"ts": now.isoformat(), "token_est": 100}),
                '{"ts": "invalid-date-format"}',
                json.dumps({"ts": now.isoformat(), "token_est": 200}),
            ]
        )
    )
    assert compute_avg_inject_token(log) == 150.0  # (100+200)/2


def test_avg_inject_token_zero_count_returns_none(tmp_path: Path):
    """所有记录都在窗口外 → None。"""
    from impl.stats_metrics import compute_avg_inject_token

    log = tmp_path / "test.jsonl"
    old = datetime.now(UTC) - timedelta(days=365)
    log.write_text(json.dumps({"ts": old.isoformat(), "token_est": 100}))
    assert compute_avg_inject_token(log, days=7) is None


# ── compute_dedup_rate ──────────────────────────────────────────────────────


def test_dedup_rate_returns_none_when_table_missing():
    """l1_dispositions 表不存在 → None(不报错)。"""
    from impl.stats_metrics import compute_dedup_rate

    conn = sqlite3.connect(":memory:")
    assert compute_dedup_rate(conn) is None
    conn.close()


def test_dedup_rate_returns_none_when_outcome_column_missing():
    """l1_dispositions 表存在但无 outcome 列 → None(降级路径)。"""
    from impl.stats_metrics import compute_dedup_rate

    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE l1_dispositions (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO l1_dispositions VALUES ('d1', '2026-06-01')")
    assert compute_dedup_rate(conn) is None
    conn.close()


def test_dedup_rate_calculation():
    """outcome 列存在,有 duplicate/merged 行 → 计算比例。"""
    from impl.stats_metrics import compute_dedup_rate

    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE l1_dispositions (
            id TEXT PRIMARY KEY,
            outcome TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 2 dedup / 4 total in last 7 days = 0.5
    now = datetime.now(UTC)
    rows = [
        ("d1", "duplicate", (now - timedelta(days=1)).isoformat()),
        ("d2", "merged", (now - timedelta(days=2)).isoformat()),
        ("d3", "kept", (now - timedelta(days=3)).isoformat()),
        ("d4", "kept", (now - timedelta(days=4)).isoformat()),
        # 超出 7 天
        ("d5", "duplicate", (now - timedelta(days=30)).isoformat()),
    ]
    for r in rows:
        conn.execute("INSERT INTO l1_dispositions VALUES (?, ?, ?)", r)
    rate = compute_dedup_rate(conn, days=7)
    assert rate == 0.5  # 2 dedup / 4 in-window
    conn.close()


def test_dedup_rate_zero_rows_returns_none():
    """窗口内 0 行 → None(避免除零)。"""
    from impl.stats_metrics import compute_dedup_rate

    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE l1_dispositions (
            id TEXT PRIMARY KEY,
            outcome TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    assert compute_dedup_rate(conn) is None
    conn.close()


# ── compute_hit_rate(Sprint 0 回归测试)──────────────────────────────────────


def _make_chunks_schema(conn):
    """建一个最小 chunks 表,字段类型与生产 schema 一致(Julianday 浮点)。"""
    conn.execute("""
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            content TEXT,
            usage_count INTEGER DEFAULT 0,
            created_at REAL DEFAULT (julianday('now')),
            last_used_at REAL
        )
    """)


def test_hit_rate_zero_rows():
    """空表 → None(避免除零)。"""
    from impl.stats_metrics import compute_hit_rate

    conn = sqlite3.connect(":memory:")
    _make_chunks_schema(conn)
    assert compute_hit_rate(conn) is None
    conn.close()


def test_hit_rate_regression_julianday_vs_datetime():
    """Sprint 0 回归:datetime() vs julianday() 浮点比较永远 0%。

    模拟生产 schema(REAL DEFAULT julianday('now'))。旧实现用
    `last_used_at > datetime('now', '-30 days')` 会得到 0%,因为 SQLite
    字符串 vs 浮点比较按类型亲和转换后永远不等。新实现用
    `last_used_at > julianday('now', '-30 days')` 同类型比较,能正确命中。
    """
    from impl.stats_metrics import compute_hit_rate

    conn = sqlite3.connect(":memory:")
    _make_chunks_schema(conn)
    # 显式设 created_at 为 60 天前,确保只有 last_used_at 决定命中
    old_created = (datetime.now(UTC) - timedelta(days=60)).timestamp() / 86400 + 2440587.5
    recent = (datetime.now(UTC) - timedelta(days=5)).timestamp() / 86400 + 2440587.5
    for i in range(3):
        conn.execute(
            "INSERT INTO chunks (id, content, usage_count, created_at, last_used_at) VALUES (?, ?, 1, ?, ?)",
            (i, f"recent-{i}", old_created, recent),
        )
    for i in range(3, 10):
        conn.execute(
            "INSERT INTO chunks (id, content, usage_count, created_at, last_used_at) VALUES (?, ?, 1, ?, ?)",
            (i, f"old-{i}", old_created, old_created),
        )
    rate = compute_hit_rate(conn, days=30)
    assert rate == 0.3, (
        f"expected 0.3 (3/10), got {rate!r} — likely datetime/julianday bug regressed"
    )
    conn.close()


def test_hit_rate_regression_old_code_returns_zero():
    """确认旧 buggy SQL(datetime() vs REAL 浮点)真的返回 0%,证明 bug 真实存在。

    锁住"用 datetime 比较浮点 = 永远 0%"这条防回归断言。
    """
    conn = sqlite3.connect(":memory:")
    _make_chunks_schema(conn)
    old_created = (datetime.now(UTC) - timedelta(days=60)).timestamp() / 86400 + 2440587.5
    recent = (datetime.now(UTC) - timedelta(days=5)).timestamp() / 86400 + 2440587.5
    conn.execute(
        "INSERT INTO chunks (id, content, usage_count, created_at, last_used_at) VALUES (1, 'x', 1, ?, ?)",
        (old_created, recent),
    )
    # 旧 buggy SQL: 浮点 vs 字符串永远不等
    buggy = conn.execute(
        """SELECT COUNT(*) FROM chunks
           WHERE usage_count > 0
             AND (last_used_at > datetime('now', '-30 days')
                  OR created_at > datetime('now', '-30 days'))"""
    ).fetchone()[0]
    assert buggy == 0, f"old SQL should return 0 (proving the bug exists), got {buggy}"
    conn.close()


def test_hit_rate_created_at_path():
    """last_used_at NULL 但 created_at 在窗口内 → 仍命中(created_at OR last_used_at)。"""
    from impl.stats_metrics import compute_hit_rate

    conn = sqlite3.connect(":memory:")
    _make_chunks_schema(conn)
    # 4 个全新 chunk(last_used_at NULL, created_at 今天)
    today_jd = datetime.now(UTC).timestamp() / 86400 + 2440587.5
    for i in range(4):
        conn.execute(
            "INSERT INTO chunks (id, content, usage_count, created_at, last_used_at) VALUES (?, ?, 0, ?, NULL)",
            (i, f"new-{i}", today_jd),
        )
    # 1 个 created_at 旧的 chunk(双 NULL) → 不应命中
    conn.execute(
        "INSERT INTO chunks (id, content, usage_count, created_at, last_used_at) VALUES (99, 'ancient', 0, ?, NULL)",
        (today_jd - 365,),
    )
    rate = compute_hit_rate(conn, days=30)
    assert rate == 0.8  # 4/5 (ancient 不算)
    conn.close()


def test_hit_rate_all_old():
    """全部 30 天前 → 0.0。"""
    from impl.stats_metrics import compute_hit_rate

    conn = sqlite3.connect(":memory:")
    _make_chunks_schema(conn)
    # 必须显式设 created_at 为旧值(否则 schema default = 今天 → 全命中)
    old_jd = (datetime.now(UTC) - timedelta(days=365)).timestamp() / 86400 + 2440587.5
    for i in range(5):
        conn.execute(
            "INSERT INTO chunks (id, content, usage_count, created_at, last_used_at) VALUES (?, ?, 1, ?, ?)",
            (i, f"old-{i}", old_jd, old_jd),
        )
    assert compute_hit_rate(conn, days=30) == 0.0
    conn.close()


# ── get_merge_counter ───────────────────────────────────────────────────────


def test_merge_counter_starts_at_zero():
    """新会话 → counter 从 0 开始。"""
    from impl.stats_metrics import get_merge_counter, reset_merge_counter_for_testing

    reset_merge_counter_for_testing()
    c = get_merge_counter()
    assert c["count"] == 0


def test_merge_counter_records_attempts():
    """record_merge_attempt 增加 count。"""
    from impl.stats_metrics import (
        get_merge_counter,
        record_merge_attempt,
        reset_merge_counter_for_testing,
    )

    reset_merge_counter_for_testing()
    record_merge_attempt()
    record_merge_attempt()
    record_merge_attempt()
    assert get_merge_counter()["count"] == 3


def test_merge_counter_resets_across_days():
    """跨日时 counter 应重置为 0(防止遗留)。"""
    from impl.stats_metrics import (
        _merge_counter,
        get_merge_counter,
        record_merge_attempt,
        reset_merge_counter_for_testing,
    )

    reset_merge_counter_for_testing()
    record_merge_attempt()
    record_merge_attempt()
    assert get_merge_counter()["count"] == 2

    # 模拟跨日:把 date 改成昨天
    from datetime import datetime

    _merge_counter["date"] = "2020-01-01"
    # 读时检测到跨日,返回 0
    assert get_merge_counter()["count"] == 0
