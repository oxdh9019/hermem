"""Hermem V6 - 向量检索接口(Sprint 1 任务 1.3 重构)。

V5: 单一 vec 通道,按相似度切分高/中置信
V6: 双路召回(vec + BM25/FTS5) + RRF 融合(决策 5)

阈值说明(bge-m3 实测相似度分布):
- HIGH=0.70(实测相关查询最高 0.80,0.85 几乎不可达)
- MEDIUM=0.50(原 0.65 偏高,截断边缘候选)
- RRF k=60(Hindsight 论文公式 15 论证)

复用组件:
- hermem_vectors.npy(Phase 2 向量库)
- hermem.db chunks 表
- hermem.db chunks_fts(SQLite FTS5 虚表,Phase 2 已建)
- impl.vectorstore(底层余弦检索)
- impl.database(SQLite 查询)
"""

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

# ── 工具函数 ────────────────────────────────────────────────────

def normalize_query(q: str) -> str:
    """Sprint 4 修偏差 1 根因(2026-06-12):去问号 + 问句尾词,避免 BM25 重排。

    例子:
        'ds2api 工具怎么用？' -> 'ds2api 工具'
        '连环画三视图生成最佳实践是什么？' -> '连环画三视图生成最佳实践'
        'Hermem V5 核心方案是什么？' -> 'Hermem V5 核心方案'

    Sprint 4 决策 1:实测 +15% Recall@5(38.2% → 53.2%)。
    修根因 2026-06-12:从评测脚本提到 search_with_tier 内置,所有调用方零改动。
    """
    if not q:
        return q
    q = q.replace('?', '').replace('？', '').strip()
    for suffix in ['是什么', '什么', '怎么用', '如何', '哪些', '哪种']:
        if q.endswith(suffix):
            q = q[:-len(suffix)]
    return q.strip()


# ── 路径 & 导入 ───────────────────────────────────────────────────
HERMEM_PHASE3 = Path(__file__).parent
sys.path.insert(0, str(HERMEM_PHASE3))

from impl import config
from impl.database import get_chunk_by_id
from impl.temporal_parser import parse_relative_time
from impl.vectorstore import cosine_topk

# ── RRF 配置(决策 5)────────────────────────────────────────────
RRF_K = 60  # Hindsight 论文公式 15 推荐值


# ── 向量加载(复用 vectorstore 缓存)───────────────────────────────


def load_vector_matrix() -> np.ndarray:
    """加载完整向量矩阵(进程内缓存)。"""
    from impl.vectorstore import _load_vectors

    return _load_vectors()


# ── BM25 通道(SQLite FTS5)──────────────────────────────────────


def hermem_search_bm25(
    query: str,
    top_k: int = 10,
    time_range: tuple[datetime, datetime] | None = None,
) -> list[dict]:
    """FTS5 BM25 检索,返回 [{chunk_id, content, session_id, chunk_type, bm25_rank}]。

    Args:
        query: 原始查询文本
        top_k: 返回条数上限
        time_range: (start, end) 时间区间;None = 不过滤
            注意:chunks.created_at 是 julianday 浮点,比较必须用 julianday()
    """
    if not query or not query.strip():
        return []
    try:
        from impl.database import get_db

        with get_db() as conn:
            # FTS5 MATCH — 用 OR 关键词(简单分词)
            # 注:FTS5 内部用 porter 词干 + unicode61 分词
            # 简单做法:按空白分词后 OR
            tokens = re.findall(r"[\w\u4e00-\u9fff]+", query)
            if not tokens:
                return []
            fts_query = " OR ".join(tokens)

            if time_range is not None:
                start, end = time_range
                # julianday(ISO 字符串) = 浮点,与 chunks.created_at 直接比较
                rows = conn.execute(
                    """SELECT c.id, c.session_id, c.content, c.chunk_type,
                              rank
                       FROM chunks_fts f
                       JOIN chunks c ON c.id = f.rowid
                       WHERE chunks_fts MATCH ?
                         AND c.created_at >= julianday(?)
                         AND c.created_at <  julianday(?)
                       ORDER BY rank
                       LIMIT ?""",
                    (
                        fts_query,
                        start.isoformat() if hasattr(start, "isoformat") else str(start),
                        end.isoformat() if hasattr(end, "isoformat") else str(end),
                        top_k,
                    ),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT c.id, c.session_id, c.content, c.chunk_type,
                              rank
                       FROM chunks_fts f
                       JOIN chunks c ON c.id = f.rowid
                       WHERE chunks_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (fts_query, top_k),
                ).fetchall()
    except Exception:
        # FTS5 失败不阻断主流程
        return []

    results = []
    for rank_idx, row in enumerate(rows):
        chunk_id, session_id, content, chunk_type, bm25_rank = row
        results.append(
            {
                "chunk_id": chunk_id,
                "content": content,
                "session_id": session_id,
                "chunk_type": chunk_type,
                "bm25_rank": rank_idx + 1,  # 1-indexed
                "bm25_native_rank": bm25_rank,  # FTS5 内部 rank(越负越相关)
            }
        )
    return results


# ── 核心检索接口 ──────────────────────────────────────────────────


def hermem_search_vector(
    query_embedding: np.ndarray,
    top_k: int = 5,
    threshold: float | None = None,
    time_range: tuple[datetime, datetime] | None = None,
) -> list[dict]:
    """
    向量检索,返回相似度 ≥ threshold 的 chunk,按相似度降序。

    Args:
        query_embedding: 查询向量(1024 维,numpy 数组)
        top_k: 返回条数上限
        threshold: 相似度阈值(默认 ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM)
        time_range: (start, end) 时间区间;None = 不过滤

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

    # 批量查询 chunk 信息(避免 N 次单独查询)
    indices = [idx for idx, score in raw_results if score >= threshold]
    if not indices:
        return []

    # 批量获取 chunk(通过 vec_index = embedding_index)
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
        # Temporal 过滤(Sprint 1.5)
        if time_range is not None and not _chunk_in_time_range(chunk, time_range):
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
    query: str | None = None,
    query_embedding: np.ndarray | None = None,
    top_k: int = 3,
    time_range: tuple[datetime, datetime] | None = None,
) -> tuple[list[dict], list[dict]]:
    """V6 Sprint 1 任务 1.3:双路召回(vec + BM25) + RRF 融合 + Temporal 过滤。

    Args:
        query: 原始查询文本(BM25 通道用;vec 通道自动 encode)
        query_embedding: 预计算 vec(可选;None 时从 query 自动 encode)
        top_k: 每个层级返回条数上限
        time_range: (start, end) 时间区间或 None
            - 显式传:直接用
            - 传 None 但 query 非空:自动调 temporal_parser 解析

    Returns:
        (high_tier, medium_tier): 两个列表,按 RRF 分数降序

    RRF 公式(决策 5):
        rrf(d) = 1/(K + rank_vec(d)) + 1/(K + rank_bm25(d))
        K = 60(Hindsight 论文公式 15)
        未出现的 doc:该通道分数为 0
    """
    # Sprint 4 修偏差 1 根因(2026-06-12):query 预处理内置
    # BM25/FTS5 对问句尾词("是/什么/怎么")敏感,预处理后 +15% Recall@5
    if query:
        query = normalize_query(query)

    # 自动 Temporal 解析
    if time_range is None and query:
        time_range = parse_relative_time(query)

    # 1. Encode query(若未提供)
    if query_embedding is None:
        if not query:
            return [], []
        try:
            query_embedding = encode_query(query)
        except Exception:
            # 编码失败 → 退化为仅 BM25
            query_embedding = None

    # 2. vec 通道
    vec_results = []
    if query_embedding is not None:
        try:
            vec_results = hermem_search_vector(
                query_embedding,
                top_k=top_k * 3,  # 多取些,RRF 后过滤
                threshold=config.ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM,
                time_range=time_range,
            )
            for r in vec_results:
                r["vec_rank"] = vec_results.index(r) + 1  # 1-indexed
        except Exception:
            vec_results = []

    # 3. BM25 通道
    bm25_results = []
    if query:
        try:
            bm25_results = hermem_search_bm25(
                query,
                top_k=top_k * 3,
                time_range=time_range,
            )
            for r in bm25_results:
                r["bm25_rank"] = bm25_results.index(r) + 1
        except Exception:
            bm25_results = []

    # 4. RRF 融合
    rrf_scores: dict[str, float] = {}
    chunk_data: dict[str, dict] = {}

    for r in vec_results:
        cid = r["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (RRF_K + r["vec_rank"])
        chunk_data[cid] = r

    for r in bm25_results:
        cid = r["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (RRF_K + r["bm25_rank"])
        # 优先用 vec 的 chunk_data(有更多字段),否则用 bm25 的
        if cid not in chunk_data:
            chunk_data[cid] = r

    # 5. 排序 + 阈值切分
    sorted_chunks = sorted(
        chunk_data.values(),
        key=lambda c: rrf_scores[c["chunk_id"]],
        reverse=True,
    )

    high = []
    medium = []
    # 阈值:沿用 V5 配置,但作用在 RRF 分数上(典型范围 0.005-0.033)
    # HIGH=0.70 → vec 单路阈值;RRF 等价约 0.02+
    # 保守做法:高置信 = 双路命中;中置信 = 单路命中 + RRF >= 0.01
    for c in sorted_chunks:
        cid = c["chunk_id"]
        rrf = rrf_scores[cid]
        in_vec = any(r["chunk_id"] == cid for r in vec_results)
        in_bm25 = any(r["chunk_id"] == cid for r in bm25_results)
        # 高置信:双路都命中 + RRF >= 0.025
        if in_vec and in_bm25 and rrf >= 0.025:
            high.append({**c, "rrf_score": rrf})
        # 中置信:任一通道命中 + RRF >= 0.01
        elif rrf >= 0.01:
            medium.append({**c, "rrf_score": rrf})

    return high[:top_k], medium[:top_k]


# ── 辅助 ──────────────────────────────────────────────────────────


def _get_chunk_by_vec_index(vec_index: int) -> dict | None:
    """根据 vec_index(= embedding_index = npy 行号)查找 chunk。"""
    from impl.database import get_chunk_by_vec_index as _get

    return _get(vec_index)


def _chunk_in_time_range(chunk: dict, time_range: tuple[datetime, datetime]) -> bool:
    """检查 chunk.created_at 是否在 [start, end) 区间内。

    chunk.created_at 是 julianday 浮点,直接与 datetime 对象比较不靠谱,
    必须用 julianday() SQL 转换或转回 datetime。这里我们读 created_at 后转 datetime。
    """
    created = chunk.get("created_at")
    if created is None:
        return True  # 无法判断,放行
    # chunk.created_at 是 julianday 浮点 → 转 datetime
    # julianday epoch: 公元前 4714-11-24
    # datetime.fromtimestamp(jd, ...) 不行;julianday → unix: (jd - 2440587.5) * 86400
    try:
        from impl.database import get_db

        with get_db() as conn:
            row = conn.execute("SELECT julianday(?) AS jd", (created,)).fetchone()
            jd = row[0] if row else None
        if jd is None:
            return True
        # 区间比对: jd >= start_jd AND jd < end_jd
        start, end = time_range
        # 转 datetime 为 julianday 浮点
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(":memory:")
        cur = conn.cursor()
        s_jd = cur.execute("SELECT julianday(?)", (start.isoformat(),)).fetchone()[0]
        e_jd = cur.execute("SELECT julianday(?)", (end.isoformat(),)).fetchone()[0]
        return s_jd <= jd < e_jd
    except Exception:
        return True  # 出错放行,不阻断


def encode_query(text: str) -> np.ndarray:
    """将文本编码为向量(使用配置的 embedding 模型)。"""
    from impl.embedding import get_embedding_cached

    emb = get_embedding_cached(text)
    if emb and emb[0]:
        return np.array(emb[0], dtype=np.float32)
    raise RuntimeError(f"Failed to encode query: {text!r}")
