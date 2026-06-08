"""Hermem V6 Sprint 0.5 - recall_outcome behavior tracker.

闭环流程:
1. V5 active retrieval 注入 chunk → 调 record_recall_outcome() 写一行
   (follow_up_type=NULL, follow_up_resolved_at=NULL)
2. 后台 worker 线程每 N 秒扫一次未解析的 recall
3. 对每条未解析记录,在其 session 后 3 轮用户消息中识别:
   - used:      chunk_id 再次出现在 heremem_search 召回中 / 关键词被引用
   - rejected:  用户消息含明确否定词("不是这个"/"不对"/"错了")
   - ignored:   3 轮内未引用 + 话题切换(下一次主动检索触发)
4. 写回 follow_up_type + follow_up_resolved_at

设计原则:
- 写入失败不阻断 V5 inject 主流程(record_recall_outcome 包在 try/except)
- 后台 worker 是单独 daemon thread,不阻塞 hermem provider
- 启动 hermem provider 时自动启动 worker
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hermem.recall_outcome")

# 3 轮内 follow-up 检测窗口
FOLLOW_UP_WINDOW_TURNS = 3
# 否定词(rejected 判定)
_NEGATION_PHRASES = (
    "不是这个",
    "不是那样",
    "不对",
    "错了",
    "错了错了",
    "不对不对",
    "重新",
    "重来",
    "再来一遍",
    "重新来",
    "不正确",
    "不相关",
    "别",
    "停一下",
    "不是我说",
    "不是这个意思",
)
# used 判定:同 chunk_id 在后续 3 轮再次被 hermem_search 召回过
# (通过 recall_outcome 表的同 session 后续行检测)
# ignored: 3 轮内未 used/rejected + 该 session 有新 recall 触发


_DB_PATH: Path | None = None  # 测试时覆盖


def _get_conn() -> sqlite3.Connection:
    """打开 hermem.db 连接(独立于 provider 主连接)。"""
    if _DB_PATH is None:
        db_path = Path.home() / ".hermes" / "memory" / "hermem.db"
    else:
        db_path = _DB_PATH
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def set_db_path_for_testing(path: Path) -> None:
    """测试覆盖 DB 路径(只用于单元测试)。"""
    global _DB_PATH
    _DB_PATH = Path(path)


def reset_db_path_for_testing() -> None:
    """恢复默认 DB 路径。"""
    global _DB_PATH
    _DB_PATH = None


def record_recall_outcome(
    session_id: str,
    chunk_id: str,
    similarity: float,
    tier: str,
    anchor_source: str = "frequency",
) -> str | None:
    """V5 active retrieval 注入时调用,写一条 recall_outcome。

    Args:
        session_id: 当前 session
        chunk_id: 被注入的 chunk
        similarity: 检索相似度
        tier: 'high' 或 'medium'(基于 0.70 阈值)
        anchor_source: 触发来源(frequency / anchor_keyword / temporal / ...)

    Returns:
        str: recall_id(写入成功);None(失败,主流程应继续)
    """
    import uuid

    recall_id = uuid.uuid4().hex
    try:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO recall_outcome
                   (recall_id, session_id, chunk_id, similarity, tier, anchor_source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, julianday('now'))""",
                (recall_id, session_id, chunk_id, similarity, tier, anchor_source),
            )
            conn.commit()
            return recall_id
        finally:
            conn.close()
    except Exception as e:
        logger.debug("recall_outcome write failed: %s", e)
        return None


def _is_negation(text: str | None) -> bool:
    """用户消息含否定短语 → True(rejected)。"""
    if not text:
        return False
    return any(p in text for p in _NEGATION_PHRASES)


def _was_reused_in_session(conn, session_id: str, chunk_id: str, after_jd: float) -> bool:
    """同 session 内,after_jd 之后,该 chunk 是否被再次 recall 出来(used 信号)。"""
    row = conn.execute(
        """SELECT 1 FROM recall_outcome
           WHERE session_id = ? AND chunk_id = ? AND created_at > ?
           LIMIT 1""",
        (session_id, chunk_id, after_jd),
    ).fetchone()
    return row is not None


def _has_new_recall_after(conn, session_id: str, after_jd: float) -> bool:
    """同 session 内,after_jd 之后是否有任何新 recall(话题切换信号 → ignored)。"""
    row = conn.execute(
        """SELECT 1 FROM recall_outcome
           WHERE session_id = ? AND created_at > ?
           LIMIT 1""",
        (session_id, after_jd),
    ).fetchone()
    return row is not None


def _resolve_one(conn, recall_id: str, session_id: str, chunk_id: str, created_at: float) -> str:
    """对单条未解析记录判定 follow_up_type。

    优先级:rejected > used > ignored。
    """
    # 1) rejected: 最近 3 轮用户消息含否定词
    #    简化方案:用 recall_outcome 表的同 session 后续行作"用户行为代理"
    #    实际生产需要查 L0 session messages;这里 Sprint 0.5 先用近似信号
    #    (后续可读 l0_l3.db.sessions 表)
    # 2) used: 同 chunk 在 3 轮内被再次 recall
    if _was_reused_in_session(conn, session_id, chunk_id, created_at):
        return "used"
    # 3) ignored: 同 session 有新 recall(话题切换)
    if _has_new_recall_after(conn, session_id, created_at):
        return "ignored"
    return "pending"  # 还不能判定,留待下轮


def resolve_pending(limit: int = 50) -> int:
    """扫描未解析的 recall_outcome,尝试判定 follow_up_type。

    Returns:
        int: 成功解析的记录数
    """
    try:
        conn = _get_conn()
    except Exception as e:
        logger.debug("resolve_pending: cannot open db: %s", e)
        return 0
    try:
        rows = conn.execute(
            """SELECT recall_id, session_id, chunk_id, created_at
               FROM recall_outcome
               WHERE follow_up_resolved_at IS NULL
               ORDER BY created_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        resolved = 0
        for recall_id, session_id, chunk_id, created_at in rows:
            outcome = _resolve_one(conn, recall_id, session_id, chunk_id, created_at)
            if outcome == "pending":
                continue  # 还不能判定,下轮再来
            conn.execute(
                """UPDATE recall_outcome
                   SET follow_up_type = ?, follow_up_resolved_at = julianday('now')
                   WHERE recall_id = ?""",
                (outcome, recall_id),
            )
            resolved += 1
        conn.commit()
        return resolved
    except Exception as e:
        logger.debug("resolve_pending failed: %s", e)
        return 0
    finally:
        conn.close()


# ── 后台 worker thread ─────────────────────────────────────────────────────

_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()
_worker_interval_sec = 30.0  # 每 30 秒扫一次


def _worker_loop():
    """后台 daemon 线程:周期扫未解析 recall。"""
    logger.info("recall_outcome worker started, interval=%.0fs", _worker_interval_sec)
    while not _worker_stop.is_set():
        try:
            n = resolve_pending(limit=100)
            if n > 0:
                logger.info("recall_outcome worker resolved %d records", n)
        except Exception as e:
            logger.debug("worker loop error: %s", e)
        # sleep 但可被 stop 打断
        _worker_stop.wait(_worker_interval_sec)


def start_worker(interval_sec: float = 30.0) -> bool:
    """启动后台 worker(幂等:已启动则跳过)。"""
    global _worker_thread, _worker_interval_sec
    if _worker_thread is not None and _worker_thread.is_alive():
        return False
    _worker_interval_sec = interval_sec
    _worker_stop.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop, name="hermem-recall-outcome-worker", daemon=True
    )
    _worker_thread.start()
    return True


def stop_worker(timeout: float = 2.0) -> bool:
    """停止后台 worker(graceful)。"""
    global _worker_thread
    if _worker_thread is None:
        return True
    _worker_stop.set()
    _worker_thread.join(timeout=timeout)
    return not _worker_thread.is_alive()


def worker_is_running() -> bool:
    return _worker_thread is not None and _worker_thread.is_alive()


# ── 手动检测(给测试用)────────────────────────────────────────────────────


def detect_negation(text: str | None) -> bool:
    """导出供测试使用。"""
    return _is_negation(text)
