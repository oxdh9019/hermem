#!/usr/bin/env python3
"""
Hermem V5.5 - L4 反思层核心逻辑

从 prediction_errors 归纳元记忆，写入 l4_reflections 表。

Usage:
    from impl.l4_reflection import synthesize_reflection, get_l4_reflections
"""

import sys
from pathlib import Path

# ── 数据库路径 ─────────────────────────────────────────────────────────────────
HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
L0L3_DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"

# ── 配置常量 ───────────────────────────────────────────────────────────────────
L4_MIN_ERRORS_FOR_REFLECTION = 3  # 至少 3 条 error 才归纳
L4_REFLECTION_TTL_DAYS = 14  # 14 天 TTL
L4_PROMPT_MAX_CHARS = 150  # 不超过 150 字
L4_MAX_SOURCE_ERRORS = 50  # 最多读取 50 条


# ── LLM 入口 ───────────────────────────────────────────────────────────────────


def _get_llm_helper():
    # v5.5/impl/ → v5.5/ → phase3/ → phase3/impl/
    phase3_path = str(Path(__file__).parent.parent.parent)
    if phase3_path not in sys.path:
        sys.path.insert(0, phase3_path)
    from impl.llm_helper import call_llm_with_fallback

    return call_llm_with_fallback


# ── 数据库操作 ─────────────────────────────────────────────────────────────────


def _get_db():
    import sqlite3

    conn = sqlite3.connect(str(HERMEM_DB))
    conn.row_factory = sqlite3.Row
    return conn


def get_yesterday_errors() -> list[dict]:
    """读取昨天的 prediction_errors"""
    conn = _get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, context, error_type, surprise_level, created_at
            FROM prediction_errors
            WHERE created_at >= julianday('now', '-1 day')
              AND created_at < julianday('now')
            ORDER BY surprise_level DESC
            LIMIT ?
        """,
            (L4_MAX_SOURCE_ERRORS,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def synthesize_reflection(errors: list[dict]) -> str | None:
    """
    用 LLM 从错误记录归纳元记忆。

    Args:
        errors: prediction_errors 列表（dict with context, error_type, surprise_level）

    Returns:
        归纳出的元记忆文本，或 None（失败/不足3条）
    """
    if not errors:
        return None
    if len(errors) < L4_MIN_ERRORS_FOR_REFLECTION:
        return None

    error_summary = "\n".join(
        [f"- [{e['surprise_level']:.2f}] {e['error_type']}: {e['context'][:100]}" for e in errors]
    )

    prompt = f"""你是一个记忆分析专家。从以下预测错误记录中归纳出用户交互模式的元记忆描述。

要求：
- 用中文
- 不超过 150 字（硬限制，超出截断）
- 直接描述，不要"根据分析"这类废话开头
- 重点：用户的偏好、习惯、期望（不是描述错误本身）

错误记录：
{error_summary}

元记忆（不超过150字）："""

    call_llm = _get_llm_helper()
    response = call_llm(prompt, max_tokens=200)
    if not response:
        return None

    # 硬截断
    return response.strip()[:L4_PROMPT_MAX_CHARS]


def write_reflection(reflection_text: str, source_errors: int, confidence: float) -> int | None:
    """
    将 L4 reflection 写入 l4_reflections 表。

    Returns:
        新增记录的 id，或 None（失败）
    """
    conn = _get_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO l4_reflections (reflection_text, source_errors, confidence, expires_at)
            VALUES (?, ?, ?, julianday('now', '+? days'))
            """,
            (reflection_text, source_errors, confidence, L4_REFLECTION_TTL_DAYS),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def write_reflection_immediate(
    reflection_text: str,
    session_id: str = "",
) -> int | None:
    """V6 Sprint 3 任务 3.5: 写入即时反思(reflect_immediate 路径)。

    跟 write_reflection 的差异:
    - source_errors = 0(标记为"非批量错误反思",即 reflect_immediate 路径)
    - confidence 固定 0.7(reflect_immediate 无 errors 计数,中位置信)
    - session_id 暂记在 reflection_text 头(V6 schema 暂未加 session_id 列)
    """
    # 把 session_id 编进 reflection_text 头(后续 Sprint 4 评估可加 session_id 列)
    text = reflection_text
    if session_id:
        text = f"[session={session_id}] " + text
    return write_reflection(
        reflection_text=text,
        source_errors=0,  # 0 = reflect_immediate
        confidence=0.7,
    )


def get_l4_reflections(max_count: int = 3) -> list[dict]:
    """获取活跃的 L4 reflection，供 warmup 注入"""
    conn = _get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, reflection_text, confidence, created_at, injected_count
            FROM l4_reflections
            WHERE expires_at IS NULL OR expires_at > julianday('now')
            ORDER BY confidence DESC, created_at DESC
            LIMIT ?
        """,
            (max_count,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_reflection_injected(reflection_id: int) -> None:
    """标记 reflection 已注入，更新计数"""
    conn = _get_db()
    try:
        conn.execute(
            """
            UPDATE l4_reflections
            SET injected_count = injected_count + 1,
                last_injected_at = julianday('now')
            WHERE id = ?
        """,
            (reflection_id,),
        )
        conn.commit()
    finally:
        conn.close()


def run_daily_reflection() -> dict:
    """
    每日 L4 反思主流程：
    1. 读取昨天 errors
    2. 归纳元记忆
    3. 写入 l4_reflections

    Returns:
        {"status": "ok"|"skipped"|"error", "reflection_id"|"reason": ...}
    """
    errors = get_yesterday_errors()

    if len(errors) < L4_MIN_ERRORS_FOR_REFLECTION:
        return {
            "status": "skipped",
            "reason": f"昨天错误记录 {len(errors)} 条，少于 {L4_MIN_ERRORS_FOR_REFLECTION} 条，跳过反思",
        }

    reflection_text = synthesize_reflection(errors)
    if not reflection_text:
        return {"status": "error", "reason": "LLM 归纳失败"}

    confidence = min(len(errors) / 50, 1.0)
    reflection_id = write_reflection(reflection_text, len(errors), confidence)

    return {
        "status": "ok",
        "reflection_id": reflection_id,
        "source_errors": len(errors),
        "reflection_text": reflection_text[:80],
    }
