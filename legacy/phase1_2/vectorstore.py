"""Hermem Phase 2 - NumPy 向量存储层。

职责：
- hermem_vectors.npy 的持久化（追加写入 + 原子更新）
- hermem_meta.json 的元数据管理
- 余弦相似度 top-k 检索（NumPy 向量化计算）
- 与 SQLite chunks 表的 vec_index 映射
"""

import json
import logging
import math
import os
import shutil
from pathlib import Path
from typing import Optional

import numpy as np

# ── 路径配置 ────────────────────────────────────────────
HERMEM_DIR = Path.home() / ".hermes" / "memory"
HERMEM_DIR.mkdir(parents=True, exist_ok=True)

VEC_PATH = HERMEM_DIR / "hermem_vectors.npy"
META_PATH = HERMEM_DIR / "hermem_meta.json"

logger = logging.getLogger(__name__)

# ── 全局状态 ────────────────────────────────────────────
_vectors: Optional[np.ndarray] = None
_meta: dict = {"version": "1.0", "dim": 1024, "next_index": 0}


def _load_meta() -> dict:
    """加载或初始化元数据。"""
    global _meta
    if META_PATH.exists():
        with open(META_PATH) as f:
            _meta = json.load(f)
    else:
        _meta = {"version": "1.0", "dim": 1024, "next_index": 0}
        _write_meta()
    return _meta


def _write_meta():
    """写回元数据文件（幂等）。"""
    with open(META_PATH, "w") as f:
        json.dump(_meta, f)


def _load_vectors() -> np.ndarray:
    """加载向量矩阵（惰性加载，进程内缓存）。"""
    global _vectors
    if _vectors is None:
        if VEC_PATH.exists():
            _vectors = np.load(VEC_PATH)
        else:
            _vectors = np.empty((0, 1024), dtype=np.float32)
    return _vectors


def _invalidate_cache():
    """丢弃进程内向量缓存（强制从磁盘重新加载）。"""
    global _vectors
    _vectors = None


# ── 初始化 ──────────────────────────────────────────────

def init_vectorstore():
    """初始化向量库（创建空 .npy 文件和元数据）。幂等操作。"""
    _load_meta()
    vectors = _load_vectors()
    if not VEC_PATH.exists():
        np.save(VEC_PATH, vectors)
    return {
        "total_vectors": _meta["next_index"],
        "dim": _meta["dim"],
        "path": str(VEC_PATH),
    }


# ── 追加写入 ───────────────────────────────────────────

def append_vectors(new_embeddings: list[list[float]]) -> list[int]:
    """追加向量到 npy 文件。

    Args:
        new_embeddings: 新向量列表，每项为 float 列表或 numpy 数组。

    Returns:
        vec_index 列表：如 [0, 1, 2] 表示这批向量占用的行号。
    """
    global _vectors, _meta

    meta = _load_meta()
    vectors = _load_vectors()

    new_mat = np.array(new_embeddings, dtype=np.float32)
    vectors = np.vstack([vectors, new_mat])

    start_index = meta["next_index"]
    end_index = start_index + len(new_embeddings)
    indices = list(range(start_index, end_index))

    # 原子写入：/tmp → shutil.copy2 → 清理
    tmp_file = "/tmp/hermem_vec_tmp.npy"
    np.save(tmp_file, vectors)
    shutil.copy2(tmp_file, str(VEC_PATH))
    os.remove(tmp_file)

    # 更新元数据
    meta["next_index"] = end_index
    _write_meta()

    # 更新进程内缓存
    _vectors = vectors
    _meta = meta

    logger.debug(f"追加 {len(new_embeddings)} 条向量，indices={indices}")
    return indices


def get_vector(vec_index: int) -> Optional[np.ndarray]:
    """按 vec_index 获取单个向量。"""
    vectors = _load_vectors()
    if 0 <= vec_index < len(vectors):
        return vectors[vec_index]
    return None


def get_vectors_batch(vec_indices: list[int]) -> np.ndarray:
    """批量获取向量（用于批量检索）。"""
    vectors = _load_vectors()
    return vectors[vec_indices]


# ── Top-K 检索 ─────────────────────────────────────────

def cosine_topk(
    query_vec: list[float] | np.ndarray,
    k: int = 5,
    exclude_indices: Optional[list[int]] = None,
) -> list[tuple[int, float]]:
    """余弦相似度 top-k 检索。

    Args:
        query_vec: 查询向量（1024 维）。
        k: 返回条数。
        exclude_indices: 要排除的 vec_index（可选，避免重复召回）。

    Returns:
        [(vec_index, score), ...] 按 score 降序排列。
    """
    vectors = _load_vectors()
    q = np.array(query_vec, dtype=np.float32)

    # 向量化余弦计算
    dots = vectors @ q
    norms = np.linalg.norm(vectors, axis=1) * (np.linalg.norm(q) + 1e-8)
    scores = dots / norms

    # 排除
    if exclude_indices:
        mask = np.ones(len(scores), dtype=bool)
        mask[exclude_indices] = False
        scores = scores * mask

    # 取 top-k
    top_indices = np.argsort(scores)[::-1][:k]
    return [(int(i), round(float(scores[i]), 6)) for i in top_indices]


def inner_product_topk(
    query_vec: list[float] | np.ndarray,
    k: int = 5,
    exclude_indices: Optional[list[int]] = None,
) -> list[tuple[int, float]]:
    """内积 top-k（适合归一化向量，比余弦更快）。"""
    vectors = _load_vectors()
    q = np.array(query_vec, dtype=np.float32)
    scores = vectors @ q

    if exclude_indices:
        mask = np.ones(len(scores), dtype=bool)
        mask[exclude_indices] = False
        scores = scores * mask

    top_indices = np.argsort(scores)[::-1][:k]
    return [(int(i), round(float(scores[i]), 6)) for i in top_indices]


# ── 统计 ────────────────────────────────────────────────

def get_stats() -> dict:
    """返回向量库统计信息。"""
    meta = _load_meta()
    vectors = _load_vectors()
    return {
        "total_vectors": meta["next_index"],
        "dim": meta["dim"],
        "shape": list(vectors.shape),
        "memory_bytes": int(vectors.nbytes),
    }
