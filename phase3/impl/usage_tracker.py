"""Hermem V5.5 - 检索命中统计更新模块

在每次 retrieve() 返回结果后，异步批量更新命中 chunk 的 usage_count 和 last_used_at。
不阻塞检索流程。

Usage:
    from impl.usage_tracker import update_l1_facts_usage_async, update_chunks_usage_async
"""

import logging
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 双数据库路径 ──────────────────────────────────────────────────────────────
HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
L0L3_DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"


def _now_jd(conn: sqlite3.Connection) -> float:
    """获取当前 Julian Day（SQLite julianday('now')）。"""
    return conn.execute("SELECT julianday('now')").fetchone()[0]


# ── l1_facts 更新（l0_l3.db）─────────────────────────────────────────────────


def update_l1_facts_usage_async(fact_ids: Sequence[int]):
    """异步批量更新 l1_facts 的 usage_count += 1 和 last_used_at = now。"""
    if not fact_ids:
        return
    threading.Thread(
        target=_update_l1_facts_usage,
        args=tuple(fact_ids),
        daemon=True,
    ).start()


def _update_l1_facts_usage(*fact_ids: int):
    """实际执行更新（运行在线程中）。"""
    conn = sqlite3.connect(L0L3_DB)
    try:
        now = _now_jd(conn)
        conn.executemany(
            "UPDATE l1_facts SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
            [(now, fid) for fid in fact_ids],
        )
        conn.commit()
        logger.debug("Updated usage for %d l1_facts", len(fact_ids))
    except Exception as e:
        logger.warning("Failed to update l1_facts usage: %s", e)
    finally:
        conn.close()


# ── chunks 更新（hermem.db）──────────────────────────────────────────────────


def update_chunks_usage_async(chunk_ids: Sequence[int]):
    """异步批量更新 chunks 的 usage_count += 1 和 last_used_at = now。"""
    if not chunk_ids:
        return
    threading.Thread(
        target=_update_chunks_usage,
        args=tuple(chunk_ids),
        daemon=True,
    ).start()


def _update_chunks_usage(*chunk_ids: int):
    """实际执行更新（运行在线程中）。"""
    conn = sqlite3.connect(HERMEM_DB)
    try:
        now = _now_jd(conn)
        conn.executemany(
            "UPDATE chunks SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
            [(now, cid) for cid in chunk_ids],
        )
        conn.commit()
        logger.debug("Updated usage for %d chunks", len(chunk_ids))
    except Exception as e:
        logger.warning("Failed to update chunks usage: %s", e)
    finally:
        conn.close()


# ── l1_dispositions 更新（l0_l3.db）────────────────────────────────────────


def update_l1_dispositions_usage_async(disposition_ids: Sequence[int]):
    """异步批量更新 l1_dispositions 的 last_used_at = now。"""
    if not disposition_ids:
        return
    threading.Thread(
        target=_update_l1_dispositions_usage,
        args=tuple(disposition_ids),
        daemon=True,
    ).start()


def _update_l1_dispositions_usage(*disp_ids: int):
    """实际执行更新（运行在线程中）。"""
    conn = sqlite3.connect(L0L3_DB)
    try:
        now = _now_jd(conn)
        conn.executemany(
            "UPDATE l1_dispositions SET last_used_at = ? WHERE id = ?",
            [(now, did) for did in disp_ids],
        )
        conn.commit()
        logger.debug("Updated last_used_at for %d l1_dispositions", len(disp_ids))
    except Exception as e:
        logger.warning("Failed to update l1_dispositions last_used_at: %s", e)
    finally:
        conn.close()


# ── 批量回填历史数据（一次性）─────────────────────────────────────────────────


def backfill_l1_facts_usage():
    """将所有已有 l1_facts 的 usage_count 设为 1（假设历史已使用过）。"""
    conn = sqlite3.connect(L0L3_DB)
    try:
        # 回填7天前，避免新系统冷启动时 active_demotion 误判
        result = conn.execute("""
            UPDATE l1_facts
            SET usage_count = MAX(usage_count, 1),
                last_used_at = COALESCE(last_used_at, julianday('now', '-7 days'))
            WHERE usage_count = 0
        """)
        conn.commit()
        print(f"回填了 {result.rowcount} 条历史 l1_facts")
    finally:
        conn.close()


def backfill_chunks_usage():
    """将所有已有 chunks 的 usage_count 设为 1。"""
    conn = sqlite3.connect(HERMEM_DB)
    try:
        result = conn.execute("""
            UPDATE chunks
            SET usage_count = MAX(usage_count, 1),
                last_used_at = COALESCE(last_used_at, julianday('now', '-7 days'))
            WHERE usage_count = 0
        """)
        conn.commit()
        print(f"回填了 {result.rowcount} 条历史 chunks")
    finally:
        conn.close()
