"""Hermem V5 - Active Retrieval 向量检索接口

职责：
- 基于 embedding 相似度的检索，支持分层阈值过滤
- 高置信（≥0.85）：直接注入上下文
- 中置信（0.65-0.85）：缓存记录，累积相似度

复用现有组件：
- hermem_vectors.npy（Phase 2 向量库）
- hermem.db chunks 表（包含 vec_index 映射）
- impl.vectorstore（底层余弦检索）
- impl.database（SQLite 查询）
"""

import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np

# ── 路径 & 导入 ───────────────────────────────────────────────────
HERMEM_PHASE3 = Path(__file__).parent
sys.path.insert(0, str(HERMEM_PHASE3))

from impl import config
from impl.database import get_chunk_by_id
from impl.vectorstore import cosine_topk

# ── 向量加载（复用 vectorstore 缓存）───────────────────────────────


def load_vector_matrix() -> np.ndarray:
    """加载完整向量矩阵（进程内缓存）。"""
    from impl.vectorstore import _load_vectors

    return _load_vectors()


# ── 核心检索接口 ──────────────────────────────────────────────────


def hermem_search_vector(
    query_embedding: np.ndarray,
    top_k: int = 5,
    threshold: float | None = None,
) -> list[dict]:
    """
    向量检索，返回相似度 ≥ threshold 的 chunk，按相似度降序。

    Args:
        query_embedding: 查询向量（1024 维，numpy 数组）
        top_k: 返回条数上限
        threshold: 相似度阈值（默认 ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM）

    Returns:
        [{"chunk_id", "content", "session_id", "chunk_type", "similarity", "embedding_index"}, ...]
    """
    if threshold is None:
        threshold = config.ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM

    matrix = load_vector_matrix()
    if matrix.shape[0] == 0:
        return []

    q = query_embedding.astype(np.float32)
    # cosine_topk 返回 [(vec_index, score), ...]
    raw_results = cosine_topk(q.tolist(), k=top_k * 3, exclude_indices=None)

    # 批量查询 chunk 信息（避免 N 次单独查询）
    indices = [idx for idx, score in raw_results if score >= threshold]
    if not indices:
        return []

    # 批量获取 chunk（通过 vec_index = embedding_index）
    chunks_by_idx = {}
    for idx in indices:
        chunk = _get_chunk_by_vec_index(idx)
        if chunk:
            chunks_by_idx[idx] = chunk

    results = []
    for vec_idx, score in raw_results:
        if len(results) >= top_k:
            break
        if score < threshold:
            continue
        chunk = chunks_by_idx.get(vec_idx)
        if not chunk:
            continue
        results.append(
            {
                "chunk_id": chunk["id"],
                "content": chunk["content"],
                "session_id": chunk["session_id"],
                "chunk_type": chunk["chunk_type"],
                "similarity": score,
                "embedding_index": vec_idx,
            }
        )

    return results


def search_with_tier(
    query_embedding: np.ndarray,
    top_k: int = 3,
) -> tuple[list[dict], list[dict]]:
    """
    分层检索：返回 (high_confidence, medium_confidence) 两个列表。

    高置信（≥0.85）：直接注入上下文
    中置信（0.65-0.85）：缓存记录，累积相似度

    Args:
        query_embedding: 查询向量
        top_k: 每个层级返回条数上限

    Returns:
        (high_confidence_list, medium_confidence_list)
    """
    all_results = hermem_search_vector(
        query_embedding,
        top_k=top_k * 2,  # 多取一些，留给阈值过滤
        threshold=config.ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM,
    )

    high = [r for r in all_results if r["similarity"] >= config.ACTIVE_RETRIEVAL_THRESHOLD_HIGH]
    medium = [
        r
        for r in all_results
        if config.ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM
        <= r["similarity"]
        < config.ACTIVE_RETRIEVAL_THRESHOLD_HIGH
    ]

    return high[:top_k], medium[:top_k]


# ── 辅助 ──────────────────────────────────────────────────────────


def _get_chunk_by_vec_index(vec_index: int) -> dict | None:
    """根据 vec_index（= embedding_index = npy 行号）查找 chunk。"""
    from impl.database import get_chunk_by_vec_index as _get

    return _get(vec_index)


def encode_query(text: str) -> np.ndarray:
    """将文本编码为向量（使用配置的 embedding 模型）。"""
    from impl.embedding import get_embedding_cached

    emb = get_embedding_cached(text)
    if emb and emb[0]:
        return np.array(emb[0], dtype=np.float32)
    raise RuntimeError(f"Failed to encode query: {text!r}")
