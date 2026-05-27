#!/usr/bin/env python3
"""
Hermem V5.5 - 记忆冲突检测与协商模块

当 L1 提取新事实时，检测是否与已有高置信 disposition 矛盾。
触发时机：L1 事实持久化之后（不是 sync_turn 中基于用户消息）。

Usage:
    from impl.conflict_resolver import detect_conflicts, create_pending_conflict
"""

import sqlite3
import sys
from pathlib import Path

# ── 路径 ───────────────────────────────────────────────────────────────────────
HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
L0L3_DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"
USER_PROFILE_PATH = Path.home() / ".hermes" / "memory" / "user_profile.md"

# ── 阈值 ──────────────────────────────────────────────────────────────────────
CONFLICT_SIMILARITY_THRESHOLD = 0.75
SIMPLE_CONTRADICTION_MIN_WORDS = 10  # 超过 10 词才走 LLM 判断


# ── 延迟导入 ───────────────────────────────────────────────────────────────────


def _get_llm_helper():
    # v5.5/impl/ → v5.5/ → phase3/ → phase3/impl/
    phase3_path = str(Path(__file__).parent.parent.parent)
    if phase3_path not in sys.path:
        sys.path.insert(0, phase3_path)
    from impl.llm_helper import call_llm_with_fallback

    return call_llm_with_fallback


def _get_db(hermem: bool = True):
    db_path = HERMEM_DB if hermem else L0L3_DB
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ── 矛盾检测 ───────────────────────────────────────────────────────────────────


def _simple_contradiction_rule(text_a: str, text_b: str) -> bool | None:
    """
    简单矛盾检测规则。返回 True/False 表示确定矛盾/不矛盾，None 表示无法判断。

    矛盾模式：
    - "喜欢 X" vs "讨厌/不 X"
    - "倾向 X" vs "避免/拒绝 X"
    """
    negations = ["不", "没", "无", "不是", "讨厌", "反感", "拒绝", "避免", "不要", "不想"]
    positives = ["喜欢", "爱", "倾向", "偏好", "愿意", "接受", "想要"]

    # 同时检测：text_a 有正向词，text_b 有否定词 → 矛盾
    words_a = text_a
    words_b = text_b

    has_pos_a = any(w in words_a for w in positives)
    has_neg_a = any(w in words_a for w in negations)
    has_pos_b = any(w in words_b for w in positives)
    has_neg_b = any(w in words_b for w in negations)

    if has_pos_a and has_neg_b:
        return True
    if has_neg_a and has_pos_b:
        return True

    # 简单的否定对比
    if has_pos_a and has_pos_b:
        return False  # 同向，无矛盾
    if has_neg_a and has_neg_b:
        return False  # 同向，无矛盾

    return None  # 无法判断


def _is_contradictory(text_a: str, text_b: str) -> bool:
    """
    矛盾检测：
    - 简单规则（否定词检测）优先
    - LLM fallback 仅在：简单规则无法判断 AND 两句都 >= 10 词
    """
    result = _simple_contradiction_rule(text_a, text_b)
    if result is not None:
        return result

    # 仅长文本才走 LLM fallback（节省 token）
    words_a = len(text_a.split())
    words_b = len(text_b.split())
    if words_a >= SIMPLE_CONTRADICTION_MIN_WORDS and words_b >= SIMPLE_CONTRADICTION_MIN_WORDS:
        return _llm_contradiction_check(text_a, text_b)

    return False  # 模棱两可时默认不触发冲突


def _llm_contradiction_check(text_a: str, text_b: str) -> bool:
    """LLM 语义矛盾判断（高成本，仅复杂场景用）"""
    prompt = f"""判断以下两条陈述是否语义矛盾（是/否）：

A: {text_a}
B: {text_b}

回答格式：仅回答"是"或"否"
"""
    call_llm = _get_llm_helper()
    response = call_llm(prompt, max_tokens=50)
    if not response:
        return False
    return "是" in response[:2]


# ── 向量相似度计算 ─────────────────────────────────────────────────────────────


def _get_embedding_model():
    """获取 sentence transformer 模型（延迟加载）"""
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer("BAAI/bge-small-zh")
    except Exception:
        return None


def _compute_similarity(text_a: str, text_b: str) -> float:
    """计算两段文本的余弦相似度"""
    model = _get_embedding_model()
    if not model:
        return 0.0

    import numpy as np

    emb_a = model.encode(text_a, normalize_embeddings=True).astype(np.float32)
    emb_b = model.encode(text_b, normalize_embeddings=True).astype(np.float32)
    sim = float(np.dot(emb_a, emb_b))
    return sim


# ── 冲突检测核心 ───────────────────────────────────────────────────────────────


def detect_conflicts(new_fact_text: str) -> list[dict]:
    """
    检测新事实是否与已有 disposition/user_profile 冲突。

    返回: [{"existing_fact", "similarity", "conflict_type", "existing_id"}, ...]

    触发时机：L1 事实持久化之后调用。
    """
    candidates = []

    # 1. 加载已有高置信 disposition（来自 l0_l3.db）
    conn_l0 = _get_db(hermem=False)
    try:
        rows = conn_l0.execute("""
            SELECT id, condition_text, prediction_text, confidence
            FROM l1_dispositions
            WHERE is_active = 1 AND confidence >= 0.7
        """).fetchall()
        for r in rows:
            candidates.append(
                {
                    "id": r["id"],
                    "text": f"{r['condition_text']} {r['prediction_text']}",
                    "type": "disposition",
                    "confidence": r["confidence"],
                }
            )
    finally:
        conn_l0.close()

    # 2. 加载 user_profile.md 已有条目（来自 hermem.db l4_reflections 相邻的 profile）
    if USER_PROFILE_PATH.exists():
        try:
            content = USER_PROFILE_PATH.read_text(encoding="utf-8")
            sections = content.split("\n---")
            for i, section in enumerate(sections):
                section = section.strip()
                if section:
                    candidates.append(
                        {
                            "id": f"profile_{i}",
                            "text": section,
                            "type": "user_profile",
                            "confidence": 0.8,  # profile 默认置信度
                        }
                    )
        except Exception:
            pass

    if not candidates:
        return []

    conflicts = []
    for c in candidates:
        sim = _compute_similarity(new_fact_text, c["text"])

        if sim > CONFLICT_SIMILARITY_THRESHOLD:
            # 语义相似度高，进一步检测矛盾
            if _is_contradictory(new_fact_text, c["text"]):
                conflicts.append(
                    {
                        "new_fact_text": new_fact_text,
                        "existing_fact_text": c["text"],
                        "similarity": sim,
                        "conflict_type": c["type"],
                        "existing_id": c["id"],
                    }
                )

    return conflicts


# ── Pending Conflicts 数据库操作 ──────────────────────────────────────────────


def create_pending_conflict(conflict: dict) -> int | None:
    """将冲突写入 pending_conflicts 表"""
    conn = _get_db(hermem=True)
    try:
        cur = conn.execute(
            """
            INSERT INTO pending_conflicts
            (new_fact_text, existing_fact_text, similarity, conflict_type, existing_id)
            VALUES (?, ?, ?, ?, ?)
        """,
            (
                conflict["new_fact_text"],
                conflict["existing_fact_text"],
                conflict["similarity"],
                conflict["conflict_type"],
                conflict["existing_id"],
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_pending_conflicts() -> list[dict]:
    """获取所有待处理冲突"""
    conn = _get_db(hermem=True)
    try:
        rows = conn.execute("""
            SELECT * FROM pending_conflicts
            WHERE status = 'pending'
            ORDER BY similarity DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve_conflict(conflict_id: int, resolution: str, note: str = None) -> None:
    """
    解决冲突：resolution = 'resolved_new' | 'resolved_existing' | 'dismissed'
    """
    conn = _get_db(hermem=True)
    try:
        conn.execute(
            """
            UPDATE pending_conflicts
            SET status = ?, resolution_note = ?, resolved_at = julianday('now')
            WHERE id = ?
        """,
            (resolution, note, conflict_id),
        )
        conn.commit()
    finally:
        conn.close()


def resolve_conflict_with_action(conflict_id: int, resolution: str, note: str = None) -> None:
    """
    解决冲突并执行实际数据更新。

    resolution:
      - 'resolved_new': 删除/降级旧的 existing_fact，更新 pending_conflicts status
      - 'resolved_existing': 保留旧的，标记 pending_conflicts
      - 'dismissed': 用户否认冲突，标记 pending_conflicts
    """
    conn_hermem = _get_db(hermem=True)
    conn_l0 = _get_db(hermem=False)

    try:
        row = conn_hermem.execute(
            "SELECT * FROM pending_conflicts WHERE id = ?", (conflict_id,)
        ).fetchone()
        if not row:
            return
        conflict = dict(row)

        if resolution == "resolved_new":
            # 删除或归档旧的 disposition / user_profile 条目
            if conflict["conflict_type"] == "disposition":
                conn_l0.execute(
                    "UPDATE l1_dispositions SET is_active = 0 WHERE id = ?",
                    (conflict["existing_id"],),
                )
                conn_l0.commit()
            elif conflict["conflict_type"] == "user_profile":
                _remove_user_profile_entry(conflict["existing_id"])

        # 统一更新 pending_conflicts 状态
        resolve_conflict(conflict_id, resolution, note)

    finally:
        conn_hermem.close()
        conn_l0.close()


def _remove_user_profile_entry(entry_id: str) -> None:
    """从 user_profile.md 中移除指定条目（标记为删除）"""
    if not USER_PROFILE_PATH.exists():
        return
    try:
        content = USER_PROFILE_PATH.read_text(encoding="utf-8")
        sections = content.split("\n---")
        new_sections = []
        for i, section in enumerate(sections):
            section = section.strip()
            if section and f"profile_{i}" != entry_id:
                new_sections.append(section)
        USER_PROFILE_PATH.write_text("\n---\n".join(new_sections), encoding="utf-8")
    except Exception:
        pass


# ── 协商消息生成 ───────────────────────────────────────────────────────────────


def generate_conflict_question(conflict: dict) -> str:
    """生成用户询问消息"""
    existing = conflict["existing_fact_text"][:50]
    new = conflict["new_fact_text"][:50]
    return (
        f"我注意到您之前提到「{existing}」，"
        f"现在又提到「{new}」。"
        f"这两者似乎有些出入——我应该以哪个为准？"
    )
