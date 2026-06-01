"""Hermem Phase 2 - 语义召回检索层。

职责：
- 语义搜索（向量相似度）
- FTS5 关键词搜索（中文 2-gram）
- 混合召回（RRF 融合）
- 按概念标签过滤
"""

import json
import logging
import threading
from typing import Optional

from . import database, embedding, vectorstore
from .usage_tracker import update_chunks_usage_async

logger = logging.getLogger(__name__)

# ── 检索参数 ────────────────────────────────────────────
DEFAULT_TOP_K = 5
RRF_K = 60
RRF_W_SEM = 0.65
RRF_W_KW = 0.35


# ── 语义搜索 ───────────────────────────────────────────


def semantic_search(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    chunk_type: str | None = None,
    concept_filter: list[str] | None = None,
) -> list[dict]:
    """语义召回：基于向量余弦相似度。

    Args:
        query: 查询文本。
        top_k: 返回条数。
        chunk_type: 可选，按 chunk 类型过滤（如 'session_summary'）。
        concept_filter: 可选，概念标签过滤（AND 逻辑）。

    Returns:
        记忆片段列表，按相似度降序排列。
    """
    # 1. 查询向量
    query_vec, _ = embedding.get_embedding_cached(query)

    # 2. 向量 top-k（多取一些，留给后续过滤）
    raw_k = top_k * 4
    top_results = vectorstore.cosine_topk(query_vec, k=raw_k)

    if not top_results:
        return []

    # 3. 按 vec_index 查询 SQLite 元数据
    indices = [idx for idx, _ in top_results]

    if not indices:
        return []

    placeholders = ",".join(["?"] * len(indices))

    sql = f"""
        SELECT c.id, c.session_id, c.content, c.chunk_type,
               c.concepts, c.created_at, c.source_file, c.vec_index
        FROM chunks c
        WHERE c.vec_index IN ({placeholders})
    """
    params = list(indices)

    if chunk_type:
        sql += " AND c.chunk_type = ?"
        params.append(chunk_type)

    with database.get_db() as conn:
        rows = list(conn.execute(sql, params))

    # 4. sqlite3.Row → dict
    rows = [dict(r) for r in rows]

    # 5. 按 vec_index 查询 SQLite 元数据
    index_to_score = dict(top_results)
    rows_sorted = sorted(
        rows,
        key=lambda r: index_to_score.get(r["vec_index"], 0),
        reverse=True,
    )

    # 6. 概念标签过滤（AND）
    if concept_filter:

        def concepts_include(row_concepts: str, filters: list[str]) -> bool:
            if not row_concepts:
                return False
            try:
                tags = json.loads(row_concepts)
            except Exception:
                return False
            return all(f in tags for f in filters)

        rows_sorted = [r for r in rows_sorted if concepts_include(r["concepts"], concept_filter)]

    # 7. 异步更新命中的 chunks 的 usage_count（不阻塞返回）
    if rows_sorted:
        chunk_ids = [r["id"] for r in rows_sorted[:top_k] if r["id"]]
        if chunk_ids:
            threading.Thread(
                target=update_chunks_usage_async,
                args=(chunk_ids,),
                daemon=True,
            ).start()

    return rows_sorted[:top_k]


# ── FTS5 关键词搜索 ────────────────────────────────────


def _chinese_2gram(text: str) -> list[str]:
    """中文 2-gram 分词（滑动窗口）。跳过含空格的 token 以避免 FTS5 语法错误。"""
    chars = list(text)
    if len(chars) < 2:
        return [text] if text else []
    result = []
    for i in range(len(chars) - 1):
        gram = chars[i] + chars[i + 1]
        if " " not in gram:  # 过滤含空格/控制字符的 token
            result.append(gram)
    return result


def keyword_search(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    chunk_type: str | None = None,
) -> list[dict]:
    """FTS5 关键词搜索（中文 2-gram）。

    Returns:
        记忆片段列表，按 FTS 排名降序。
    """
    tokens = _chinese_2gram(query)
    if not tokens:
        # fallback: 直接用原文
        fts_query = query
    else:
        fts_query = " OR ".join(f'"{t}"' for t in tokens)

    ",".join(["?"] * len(tokens)) if tokens else "?"

    sql = f"""
        SELECT c.id, c.session_id, c.content, c.chunk_type,
               c.concepts, c.created_at, c.source_file, c.vec_index,
               rank
        FROM chunks_fts
        JOIN chunks c ON chunks_fts.rowid = c.id
        WHERE chunks_fts MATCH ?
        {"AND c.chunk_type = ?" if chunk_type else ""}
        ORDER BY rank
        LIMIT ?
    """
    params = [fts_query] + ([chunk_type] if chunk_type else []) + [top_k]

    with database.get_db() as conn:
        rows = list(conn.execute(sql, params))

    # sqlite3.Row → dict
    rows = [dict(r) for r in rows]

    # 异步更新命中的 chunks 的 usage_count
    if rows:
        chunk_ids = [r["id"] for r in rows[:top_k] if r["id"]]
        if chunk_ids:
            threading.Thread(
                target=update_chunks_usage_async,
                args=(chunk_ids,),
                daemon=True,
            ).start()

    return rows


# ── 混合搜索 ───────────────────────────────────────────


def hybrid_search(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    chunk_type: str | None = None,
    concept_filter: list[str] | None = None,
    w_sem: float = RRF_W_SEM,
    w_kw: float = RRF_W_KW,
) -> list[dict]:
    """语义 + 关键词混合召回（RRF 融合）。

    RRF 权重: w_sem=0.65, w_kw=0.35（可通过参数覆盖）

    Returns:
        融合后的记忆片段列表，按 RRF 分值降序。
    """
    # 并行执行两路搜索
    sem_results = semantic_search(
        query,
        top_k=top_k * 2,
        chunk_type=chunk_type,
        concept_filter=concept_filter,
    )
    kw_results = keyword_search(query, top_k=top_k * 2, chunk_type=chunk_type)

    if not sem_results and not kw_results:
        return []

    # RRF 融合
    fused = _rrf_fuse(sem_results, kw_results, k=RRF_K, w_sem=w_sem, w_kw=w_kw)
    return fused[:top_k]


def _rrf_fuse(
    sem_results: list[dict],
    kw_results: list[dict],
    k: int = RRF_K,
    w_sem: float = RRF_W_SEM,
    w_kw: float = RRF_W_KW,
) -> list[dict]:
    """Reciprocal Rank Fusion 两路融合。"""
    scores: dict[int, float] = {}

    for rank, row in enumerate(sem_results):
        chunk_id = row["id"]
        scores[chunk_id] = scores.get(chunk_id, 0) + w_sem * (1.0 / (k + rank + 1))

    for rank, row in enumerate(kw_results):
        chunk_id = row["id"]
        scores[chunk_id] = scores.get(chunk_id, 0) + w_kw * (1.0 / (k + rank + 1))

    # 去重，收集完整行
    id_to_row = {}
    for row in sem_results + kw_results:
        id_to_row[row["id"]] = row

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [id_to_row[cid] for cid in sorted_ids]


# ── 便捷入口 ───────────────────────────────────────────


def search(query: str, mode: str = "hybrid", **kwargs) -> list[dict]:
    """统一搜索入口。

    Args:
        query: 查询文本。
        mode: 'semantic' | 'keyword' | 'hybrid'。
        **kwargs: 透传给各搜索函数。

    Returns:
        记忆片段列表。
    """
    if mode == "semantic":
        return semantic_search(query, **kwargs)
    elif mode == "keyword":
        return keyword_search(query, **kwargs)
    else:
        return hybrid_search(query, **kwargs)
