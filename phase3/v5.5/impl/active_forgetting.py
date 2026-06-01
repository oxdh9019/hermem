#!/usr/bin/env python3
"""
Hermem V5.5 - 生物学启发的主动遗忘模块

sleep_consolidation(): 每周日将高频召回的 L1 fact 提升为 L3 用户画像
active_demotion(): 归档 30 天未召回且置信度低的 dispositions

Usage:
    from impl.active_forgetting import sleep_consolidation, active_demotion
"""

import sys
from pathlib import Path

# ── 路径 ───────────────────────────────────────────────────────────────────────
HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
L0L3_DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"
USER_PROFILE_PATH = Path.home() / ".hermes" / "memory" / "user_profile.md"

# ── 阈值 ───────────────────────────────────────────────────────────────────────
SLEEP_USAGE_THRESHOLD = 5  # usage_count > 5
SLEEP_DAYS_THRESHOLD = 7  # last_used_at >= 7 天前
DEMOTION_DAYS = 30  # last_used_at < 30 天前
DEMOTION_MIN_CONFIDENCE = 0.6  # confidence < 0.6 才归档


# ── LLM 入口 ───────────────────────────────────────────────────────────────────


def _get_llm_helper():
    # v5.5/impl/ → v5.5/ → phase3/ → phase3/impl/
    phase3_path = str(Path(__file__).parent.parent.parent)
    if phase3_path not in sys.path:
        sys.path.insert(0, phase3_path)
    from impl.llm_helper import call_llm_with_fallback

    return call_llm_with_fallback


def _get_db(hermem: bool = True):
    import sqlite3

    db_path = HERMEM_DB if hermem else L0L3_DB
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ── Sleep Consolidation（睡眠巩固）─────────────────────────────────────────────


def sleep_consolidation() -> dict:
    """
    睡眠巩固：查询 usage_count > 5 AND last_used_at >= 7天前 的 L1 facts，
    LLM 归纳后写入 user_profile.md。

    Returns:
        {"promoted": int, "profile_text": str|None}
    """
    conn = _get_db(hermem=False)
    try:
        rows = conn.execute(
            """
            SELECT id, content, usage_count
            FROM l1_facts
            WHERE usage_count > ?
              AND last_used_at IS NOT NULL
              AND last_used_at >= julianday('now', '-' || CAST(? AS TEXT) || ' days')
            ORDER BY usage_count DESC
            LIMIT 20
        """,
            (SLEEP_USAGE_THRESHOLD, SLEEP_DAYS_THRESHOLD),
        ).fetchall()

        if not rows:
            return {"promoted": 0, "profile_text": None}

        facts = [dict(r) for r in rows]

        # LLM 归纳用户画像
        fact_text = "\n".join([f"- {f['content']}" for f in facts])
        call_llm = _get_llm_helper()
        prompt = f"""从以下高频事实归纳用户偏好。不超过80字，直接描述。

高频事实：
{fact_text}

用户画像（不超过80字）："""

        profile = call_llm(prompt, max_tokens=150)
        if not profile:
            return {"promoted": 0, "profile_text": None}

        # 写入 user_profile.md
        profile_text = profile.strip()[:80]
        try:
            USER_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(USER_PROFILE_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"\n---\n{profile_text}\n")
        except Exception:
            pass

        # 标记已提升（避免重复提升）
        ids = [f["id"] for f in facts]
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(f"UPDATE l1_facts SET status = 'promoted' WHERE id IN ({placeholders})", ids)
        conn.commit()

        return {"promoted": len(ids), "profile_text": profile_text}

    finally:
        conn.close()


# ── Active Demotion（主动降级）─────────────────────────────────────────────────


def active_demotion(min_confidence: float = DEMOTION_MIN_CONFIDENCE) -> dict:
    """
    归档 30 天未召回且置信度低的 dispositions。
    防止低频但重要的记忆（如账号密码）被误归档。

    Args:
        min_confidence: 仅归档置信度低于此值的 disposition

    Returns:
        {"demoted": int, "ids": list[int]}
    """
    conn = _get_db(hermem=False)
    try:
        rows = conn.execute(
            """
            SELECT id, condition_text, prediction_text, confidence
            FROM l1_dispositions
            WHERE is_active = 1
              AND confidence < ?
              AND (last_used_at IS NULL OR last_used_at < julianday('now', '-' || CAST(? AS TEXT) || ' days'))
        """,
            (min_confidence, DEMOTION_DAYS),
        ).fetchall()

        if not rows:
            return {"demoted": 0, "ids": []}

        ids = [r["id"] for r in rows]
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"UPDATE l1_dispositions SET is_active = 0, archived = 1 WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return {"demoted": len(ids), "ids": ids}

    finally:
        conn.close()


# ── 综合运行 ───────────────────────────────────────────────────────────────────


def run_consolidation() -> dict:
    """
    综合执行睡眠巩固 + 主动降级。

    Returns:
        {"sleep": {...}, "demotion": {...}}
    """
    sleep_result = sleep_consolidation()
    demotion_result = active_demotion()
    return {
        "sleep": sleep_result,
        "demotion": demotion_result,
    }
