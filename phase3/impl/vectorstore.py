"""Hermem Phase 2 - NumPy 向量存储层。

职责：
- hermem_vectors.npy 的持久化（追加写入 + 原子更新）
- hermem_meta.json 的元数据管理
- 余弦相似度 top-k 检索（NumPy 向量化计算）
- 与 SQLite chunks 表的 vec_index 映射

并发安全：
- 进程内：threading.Lock（_write_lock）
- 进程间：fcntl.flock（对本地 .lock 文件加锁）
  macOS 上为建议性锁，需配合进程内 Lock 使用
"""

import fcntl
import json
import logging
import math
import os
import shutil
import threading
from pathlib import Path
from typing import Optional

import numpy as np

# ── 路径配置 ────────────────────────────────────────────
HERMEM_DIR = Path.home() / ".hermes" / "memory"
HERMEM_DIR.mkdir(parents=True, exist_ok=True)

VEC_PATH   = HERMEM_DIR / "hermem_vectors.npy"
META_PATH  = HERMEM_DIR / "hermem_meta.json"
LOCK_PATH  = HERMEM_DIR / ".vector_write.lock"   # 进程锁文件（init_vectorstore 时创建）


logger = logging.getLogger(__name__)

# ── 全局状态 ────────────────────────────────────────────
_vectors: Optional[np.ndarray] = None
_meta: dict = {"version": "1.0", "dim": 1024, "next_index": 0}

# ── 锁 ─────────────────────────────────────────────────
# 进程内线程锁：保护 _vectors / _meta 缓存读写
_write_lock = threading.Lock()

# 进程间文件锁句柄（延迟打开，首次写入时初始化）
_lock_fd: Optional[int] = None


def _acquire_file_lock():
    """获取进程间独占锁（fcntl.flock）。macOS advisory only。"""
    global _lock_fd
    if _lock_fd is None:
        # 文件存在时直接打开，不使用 O_EXCL（避免 FileExistsError）
        try:
            _lock_fd = os.open(str(LOCK_PATH), os.O_RDWR)
        except FileNotFoundError:
            # init_vectorstore 还没跑，先创建锁文件
            _lock_fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(_lock_fd, fcntl.LOCK_EX)   # 阻塞直到获得锁


def _release_file_lock():
    """释放进程间锁。"""
    global _lock_fd
    if _lock_fd is not None:
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)


# ── meta 读写 ───────────────────────────────────────────

def _load_meta() -> dict:
    """加载或初始化元数据（幂等读）。"""
    global _meta
    if META_PATH.exists():
        with open(META_PATH) as f:
            _meta = json.load(f)
    else:
        _meta = {"version": "1.0", "dim": 1024, "next_index": 0}
        _write_meta()
    return _meta


def _write_meta():
    """写回元数据文件（幂等）。调用方必须持有 _write_lock。"""
    with open(META_PATH, "w") as f:
        json.dump(_meta, f)


# ── 向量缓存 ─────────────────────────────────────────────

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
    # 确保锁文件存在（避免并发写入时找不到文件）
    if not LOCK_PATH.exists():
        LOCK_PATH.touch()
    return {
        "total_vectors": _meta["next_index"],
        "dim": _meta["dim"],
        "path": str(VEC_PATH),
    }


# ── 追加写入（线程安全 + 进程安全）──────────────────────

def append_vectors(new_embeddings: list[list[float]]) -> list[int]:
    """追加向量到 npy 文件。

    并发安全：
    - 进程内：threading.Lock 串行化所有写入线程
    - 进程间：fcntl.flock 串行化所有写入进程

    Args:
        new_embeddings: 新向量列表，每项为 float 列表或 numpy 数组。

    Returns:
        vec_index 列表：如 [0, 1, 2] 表示这批向量占用的行号。
    """
    new_mat = np.array(new_embeddings, dtype=np.float32)

    # ── 进程间锁（阻塞直到获得）────────────────────────
    _acquire_file_lock()
    try:
        # ── 进程内锁（串行化同进程的所有线程）─────────
        with _write_lock:
            meta    = _load_meta()
            vectors = _load_vectors()

            # 分配索引
            start_index = meta["next_index"]
            end_index   = start_index + len(new_embeddings)
            indices = list(range(start_index, end_index))

            # 追加到内存矩阵
            combined = np.vstack([vectors, new_mat])

            # 原子写入：/tmp → copy → os.remove
            # np.save 在大文件时有概率读到部分写入的 npy，
            # 走 tmp 中转再 rename 比直接写更安全
            tmp_file = HERMEM_DIR / ".vector_write_tmp.npy"
            np.save(str(tmp_file), combined)
            os.replace(str(tmp_file), str(VEC_PATH))

            # 更新 meta（单次写入，本身原子）
            meta["next_index"] = end_index
            _write_meta()

            # 同步更新进程内缓存
            _vectors = combined
            _meta    = meta

            logger.debug(
                "append_vectors: %d vectors, indices=%s, new next_index=%d",
                len(new_embeddings), indices, end_index
            )
            return indices

    finally:
        _release_file_lock()


# ── 读取（无锁，只读操作）────────────────────────────────

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
    dots  = vectors @ q
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
    """返回向量库统计信息（无锁，读取 meta 文件）。"""
    meta = _load_meta()
    vectors = _load_vectors()
    return {
        "total_vectors": meta["next_index"],
        "dim": meta["dim"],
        "shape": list(vectors.shape),
        "memory_bytes": int(vectors.nbytes),
    }


# ── drift 检测（watchdog 用）────────────────────────────

def check_drift() -> dict:
    """检查 meta.next_index 与实际 npy 行数是否一致（直接读文件，无缓存）。"""
    # 直接读文件，不用 _load_meta()（它会复用进程内缓存）
    if META_PATH.exists():
        with open(META_PATH) as f:
            meta = json.load(f)
    else:
        meta = {"next_index": 0}
    meta_next = meta.get("next_index", 0)

    # 直接读 npy 形状
    if VEC_PATH.exists():
        # load 时 mmap_mode=None 确保每次都从磁盘读
        vecs = np.load(str(VEC_PATH), mmap_mode=None)
        npy_rows = vecs.shape[0]
    else:
        npy_rows = 0

    drift = meta_next - npy_rows
    ok    = (drift == 0)
    if ok:
        msg = f"OK (next_index={meta_next}, npy_rows={npy_rows})"
    else:
        msg = f"DRIFT: meta next_index={meta_next}, npy rows={npy_rows}, drift={drift}"
    return {
        "drift": drift,
        "meta_next": meta_next,
        "npy_rows": npy_rows,
        "ok": ok,
        "message": msg,
    }
