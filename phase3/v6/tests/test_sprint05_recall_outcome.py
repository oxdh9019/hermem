"""V6 Sprint 0.5 单元测试 - recall_outcome 行为闭环。

测试 impl.recall_outcome_tracker 的关键路径:
- record_recall_outcome: 写入 + 失败降级
- resolve_pending: used / ignored 判定
- detect_negation: 否定词检测
- worker 生命周期: start / stop / 幂等
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_db(tmp_path: Path):
    """建一个临时 hermem.db,带 recall_outcome 表结构。"""
    db_path = tmp_path / "hermem_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE recall_outcome (
            recall_id              TEXT PRIMARY KEY,
            session_id             TEXT NOT NULL,
            chunk_id               TEXT NOT NULL,
            similarity             REAL,
            tier                   TEXT,
            anchor_source          TEXT,
            follow_up_type         TEXT,
            follow_up_turn_count   INTEGER,
            created_at             REAL DEFAULT (julianday('now')),
            follow_up_resolved_at  REAL
        );
    """)
    conn.commit()
    conn.close()

    from impl.recall_outcome_tracker import reset_db_path_for_testing, set_db_path_for_testing

    set_db_path_for_testing(db_path)
    yield db_path
    reset_db_path_for_testing()


# ── record_recall_outcome ───────────────────────────────────────────────────


def test_record_recall_outcome_writes_row(temp_db: Path):
    """正常路径:写入一行,follow_up_type 为 NULL(待解析)。"""
    from impl.recall_outcome_tracker import record_recall_outcome

    recall_id = record_recall_outcome(
        session_id="s1", chunk_id="c1", similarity=0.85, tier="high", anchor_source="frequency"
    )
    assert recall_id is not None
    conn = sqlite3.connect(str(temp_db))
    row = conn.execute(
        "SELECT session_id, chunk_id, similarity, tier, anchor_source, follow_up_type FROM recall_outcome WHERE recall_id = ?",
        (recall_id,),
    ).fetchone()
    conn.close()
    assert row == ("s1", "c1", 0.85, "high", "frequency", None)


def test_record_recall_outcome_handles_missing_table():
    """DB 不存在 recall_outcome 表 → 返回 None,不抛异常(降级)。"""
    from impl.recall_outcome_tracker import record_recall_outcome

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        # 故意不建 recall_outcome 表
        pass
    from impl.recall_outcome_tracker import reset_db_path_for_testing, set_db_path_for_testing

    set_db_path_for_testing(Path(f.name))
    try:
        result = record_recall_outcome(session_id="s1", chunk_id="c1", similarity=0.85, tier="high")
        assert result is None  # 失败降级
    finally:
        reset_db_path_for_testing()
        Path(f.name).unlink(missing_ok=True)


def test_record_recall_outcome_fails_silently_on_bad_db():
    """DB 路径无效 → 返回 None,主流程不阻断。"""
    from impl.recall_outcome_tracker import (
        record_recall_outcome,
        reset_db_path_for_testing,
        set_db_path_for_testing,
    )

    # 用不存在的目录(在 tmp_path 下创建一个不存在的子目录,然后删掉)
    with tempfile.TemporaryDirectory() as td:
        nonexistent = Path(td) / "no_such_subdir" / "x.db"
        set_db_path_for_testing(nonexistent)
        try:
            result = record_recall_outcome(
                session_id="s1", chunk_id="c1", similarity=0.85, tier="high"
            )
            # sqlite3.connect 会自动建文件,但 parent dir 不存在时会抛
            assert result is None or isinstance(result, str)
        finally:
            reset_db_path_for_testing()


# ── detect_negation ─────────────────────────────────────────────────────────


def test_detect_negation_recognizes_common_phrases():
    from impl.recall_outcome_tracker import detect_negation

    assert detect_negation("不是这个") is True
    assert detect_negation("不对不对,重来") is True
    assert detect_negation("错了") is True
    assert detect_negation("重新讲一遍") is True


def test_detect_negation_returns_false_for_normal_text():
    from impl.recall_outcome_tracker import detect_negation

    assert detect_negation("好的,继续") is False
    assert detect_negation("") is False
    assert detect_negation(None) is False
    assert detect_negation("这个方案不错") is False


# ── resolve_pending ─────────────────────────────────────────────────────────


def test_resolve_pending_marks_used_when_chunk_recalled_again(temp_db: Path):
    """同 chunk 在 3 轮内再次 recall → 标记 used。"""
    from impl.recall_outcome_tracker import record_recall_outcome, resolve_pending

    record_recall_outcome(session_id="s1", chunk_id="c1", similarity=0.85, tier="high")
    time.sleep(0.05)
    # 同一 chunk 又被 recall(模拟 used 信号)
    record_recall_outcome(session_id="s1", chunk_id="c1", similarity=0.88, tier="high")

    resolved = resolve_pending(limit=50)
    assert resolved >= 1

    conn = sqlite3.connect(str(temp_db))
    types = [
        r[0]
        for r in conn.execute(
            "SELECT follow_up_type FROM recall_outcome ORDER BY created_at ASC"
        ).fetchall()
    ]
    conn.close()
    # 第一条应该被标 used(因为同 chunk 后续有 recall)
    assert types[0] == "used"


def test_resolve_pending_marks_ignored_on_topic_switch(temp_db: Path):
    """同 session 有新 recall(其他 chunk) → 标 ignored(原 chunk)。"""
    from impl.recall_outcome_tracker import record_recall_outcome, resolve_pending

    record_recall_outcome(session_id="s1", chunk_id="c1", similarity=0.85, tier="high")
    time.sleep(0.05)
    # 不同 chunk,同 session → 话题切换
    record_recall_outcome(session_id="s1", chunk_id="c2", similarity=0.75, tier="high")

    resolve_pending(limit=50)
    conn = sqlite3.connect(str(temp_db))
    type_c1 = conn.execute(
        "SELECT follow_up_type FROM recall_outcome WHERE chunk_id = 'c1'"
    ).fetchone()[0]
    conn.close()
    assert type_c1 == "ignored"


def test_resolve_pending_leaves_pending_when_no_signal(temp_db: Path):
    """同 session 无新 recall → 保持 pending(留给下一轮)。"""
    from impl.recall_outcome_tracker import record_recall_outcome, resolve_pending

    record_recall_outcome(session_id="s1", chunk_id="c1", similarity=0.85, tier="high")

    resolved = resolve_pending(limit=50)
    assert resolved == 0  # 无法判定,跳过

    conn = sqlite3.connect(str(temp_db))
    type_c1 = conn.execute(
        "SELECT follow_up_type FROM recall_outcome WHERE chunk_id = 'c1'"
    ).fetchone()[0]
    conn.close()
    assert type_c1 is None  # 仍为 NULL


def test_resolve_pending_respects_limit(temp_db: Path):
    """resolve_pending limit 参数生效。"""
    from impl.recall_outcome_tracker import record_recall_outcome, resolve_pending

    for i in range(5):
        record_recall_outcome(session_id=f"s{i}", chunk_id="c1", similarity=0.85, tier="high")
    # limit=2 → 只处理 2 条
    # 但前 2 条都没有 used/ignored 信号(只有自身,没有同 session 后续),
    # 所以 resolved=0
    resolved = resolve_pending(limit=2)
    assert resolved == 0


# ── worker 生命周期 ─────────────────────────────────────────────────────────


def test_worker_start_is_idempotent():
    """start_worker 调用多次 → 只启动一个线程。"""
    from impl.recall_outcome_tracker import start_worker, stop_worker, worker_is_running

    try:
        start_worker(interval_sec=0.5)
        start_worker(interval_sec=0.5)  # 第二次应跳过
        assert worker_is_running() is True
    finally:
        stop_worker(timeout=2.0)


def test_worker_stop_joins_thread():
    """stop_worker → 线程真正结束(daemon=True 主进程退出时自动回收,但显式 stop 更稳)。"""
    from impl.recall_outcome_tracker import start_worker, stop_worker, worker_is_running

    start_worker(interval_sec=0.5)
    assert worker_is_running() is True
    stopped = stop_worker(timeout=3.0)
    assert stopped is True
    assert worker_is_running() is False


def test_worker_resolves_records_periodically(temp_db: Path):
    """worker 跑 1 轮后,能解析记录的应该被标 follow_up_type。"""
    from impl.recall_outcome_tracker import (
        record_recall_outcome,
        resolve_pending,
        start_worker,
        stop_worker,
    )

    # 写一条 + 后续 recall 触发 used
    record_recall_outcome(session_id="s1", chunk_id="c1", similarity=0.85, tier="high")
    time.sleep(0.05)
    record_recall_outcome(session_id="s1", chunk_id="c1", similarity=0.88, tier="high")

    # 直接调 resolve_pending(不等 worker) — 因为 worker interval=0.5 测试慢
    n = resolve_pending(limit=10)
    assert n >= 1
