"""Hermem V6 Sprint 4 任务 4.5:概念权重。

每个 chunk 有 0-1 的 concept_weight(实际 1-2),反映"用户最近在关心这个概念"。
Sprint 0.5 落地的 disposition 体系已存在 (DISPOSITION_HALF_LIFE_DAYS=7),
本模块复用其常量,封装可独立测试的函数。
"""

import math
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .config import (
    DISPOSITION_HALF_LIFE_DAYS,
    DISPOSITION_BASE_WEIGHT,
    DISPOSITION_MAX_FACTOR,
)


# ── 衰减函数 ────────────────────────────────────────────

def decayed_weight(
    last_used_at: float | None,  # julianday 时间戳
    base_weight: float = DISPOSITION_BASE_WEIGHT,
    max_factor: float = DISPOSITION_MAX_FACTOR,
    half_life_days: float = DISPOSITION_HALF_LIFE_DAYS,
    now: float | None = None,
) -> float:
    """Sprint 4 任务 4.5:概念权重半衰期衰减。

    公式: weight = base + (max - base) * 0.5 ^ ((now - last_used) / half_life)
    - last_used 越近 → weight 越接近 max(最近被关心)
    - last_used 越远 → weight 越接近 base(中性 1.0)
    - 没 last_used → 1.0(中性,不加权)

    Args:
        last_used_at: julianday 时间戳(None = 1.0)
        base_weight: 中性起点(默认 1.0)
        max_factor: 最高增强(默认 2.0)
        half_life_days: 半衰期(默认 7)
        now: 当前 julianday(测试用)

    Returns:
        weight in [base, max](默认 [1.0, 2.0])
    """
    if last_used_at is None:
        return base_weight
    if now is None:
        now = time.time() / 86400  # unix → julianday(近似)
    elapsed_days = max(0.0, now - last_used_at)
    decay = math.pow(0.5, elapsed_days / half_life_days)
    return base_weight + (max_factor - base_weight) * decay


# ── 批量计算 ────────────────────────────────────────────

def get_concept_weights_for_chunks(
    chunk_ids: list[int],
    half_life_days: float = DISPOSITION_HALF_LIFE_DAYS,
) -> dict[int, float]:
    """Sprint 4 任务 4.5:批量计算 chunk_id → concept_weight 映射。

    Args:
        chunk_ids: chunk id 列表
        half_life_days: 半衰期(默认 7)

    Returns:
        {chunk_id: weight} dict
    """
    if not chunk_ids:
        return {}
    HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
    if not HERMEM_DB.exists():
        return {cid: 1.0 for cid in chunk_ids}

    con = sqlite3.connect(str(HERMEM_DB))
    try:
        placeholders = ",".join("?" * len(chunk_ids))
        rows = con.execute(
            f"SELECT id, last_used_at FROM chunks WHERE id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        return {
            cid: decayed_weight(last_used_at, half_life_days=half_life_days)
            for cid, last_used_at in rows
        }
    finally:
        con.close()


# ── 一致性检查 ────────────────────────────────────────────

def get_chunk_concept_weight(
    chunk_id: int,
    half_life_days: float = DISPOSITION_HALF_LIFE_DAYS,
) -> float:
    """Sprint 4 任务 4.5:单 chunk 权重查询。"""
    return get_concept_weights_for_chunks([chunk_id], half_life_days).get(chunk_id, 1.0)
