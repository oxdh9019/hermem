"""Hermem V6 Sprint 4 任务 4.6:重排器。

在 search_with_tier RRF 融合后,应用加权公式:
    score = cosine × recency × concept_weight × pattern_relevance

Sprint 4 简化版:recency 和 pattern_relevance 暂用 1.0(中性),
只动 concept_weight(任务 4.5 落地)。
"""

import time

from .concept_weight import decayed_weight


def rerank(
    chunks: list[dict],
    top_k: int = 3,
    half_life_days: float = 7,
    apply_concept_weight: bool = True,
) -> list[dict]:
    """Sprint 4 任务 4.6 重排:score = cosine × recency × concept_weight × pattern_relevance。

    Args:
        chunks: 已 RRF 融合的 chunk 列表(每条含 rrf_score, last_used_at)
        top_k: 返回条数
        half_life_days: concept_weight 半衰期
        apply_concept_weight: 是否应用 concept_weight(开关,默认 True)

    Returns:
        重排后的 top_k chunks(每条加 final_score 字段)
    """
    if not chunks:
        return []

    now = time.time() / 86400
    for c in chunks:
        cosine = c.get("rrf_score", 0) or 0
        recency = 1.0  # 简化版:所有 chunk 同等
        if apply_concept_weight:
            cw = decayed_weight(
                c.get("last_used_at"),
                half_life_days=half_life_days,
                now=now,
            )
        else:
            cw = 1.0
        pr = 1.0  # 简化版:所有 chunk 同等
        c["final_score"] = cosine * recency * cw * pr
    return sorted(chunks, key=lambda x: x["final_score"], reverse=True)[:top_k]
