"""Hermem V6 Sprint 3 - 解释模板库。

设计原则(V6 SPEC §3 模块 3):
- 过渡句不掩盖相似度(footer 可选 `[内部召回 · 相似度 0.91]`)
- 过渡句不臆造内容(不能"为了过渡"添加 chunk 没有的细节)
- 失败时降级到 V5,不阻断流程
- 中文优先(本地用户),英文 fallback
- 6 个模板轮转:不同 turn 用不同句式,避免机械感
- md5 seed 选模板:同一 chunk 同一 turn 同一模板,不抖动
"""

import hashlib
from typing import Optional


# ── 模板定义(6 句,中文 4 + 英文 2)──────────────────────────────────
TEMPLATES = [
    "看到您提到 {trigger},我想起 {chunk_excerpt}({relevance_hint})。需要我展开讲吗?",
    "关于 {trigger},之前有类似记录:{chunk_excerpt}({relevance_hint})。",
    "{trigger} 让我想到:{chunk_excerpt}({relevance_hint})。",
    "这让我回忆起:{chunk_excerpt}({relevance_hint})。",
    "Earlier we discussed: {chunk_excerpt_en}({relevance_hint}).",
    "FYI 相关历史:{chunk_excerpt}({relevance_hint})。",
]

RELEVANCE_HINTS = {
    # similarity 0-1 → 3 档 hint 文案
    (0.0, 0.4): "低置信",
    (0.4, 0.7): "中置信",
    (0.7, 1.01): "高置信",
}


def relevance_hint(similarity: float) -> str:
    """similarity → 3 档 hint 文案。"""
    for (lo, hi), hint in RELEVANCE_HINTS.items():
        if lo <= similarity < hi:
            return hint
    return "未知置信"


def select_template(seed: str) -> str:
    """基于 seed(turn id 或 chunk id)选模板 — 同一 chunk 同一 turn 同一模板,不抖动。"""
    idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(TEMPLATES)
    return TEMPLATES[idx]


def render_explanation(
    chunk_content: str,
    trigger: str,
    similarity: float,
    seed: str = "default",
) -> str:
    """轻量路径主函数:模板渲染一句话解释。

    Args:
        chunk_content: 命中的 chunk 内容(取前 80 字)
        trigger: 触发本次召回的用户 query 关键词
        similarity: 0-1 相似度分数
        seed: 决定用哪个模板(turn id 或 chunk id)

    Returns:
        一句话解释(中文/英文)
    """
    template = select_template(seed)
    excerpt = chunk_content[:80].rstrip()
    if excerpt and not excerpt.endswith((".", "。", "!", "?")):
        excerpt += "..."
    hint = relevance_hint(similarity)
    return template.format(
        trigger=trigger[:20],
        chunk_excerpt=excerpt,
        chunk_excerpt_en=excerpt,  # 英文模板用同一字段
        relevance_hint=hint,
    )
