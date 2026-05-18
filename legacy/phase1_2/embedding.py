"""Hermem Phase 2 - Ollama bge-m3 Embedding 层。

职责：
- 调用本地 Ollama bge-m3 生成 1024 维向量
- SHA256 缓存，避免重复 embedding 同文本
- 健康检查（Ollama 服务 + bge-m3 模型可用性）
"""

import hashlib
import pickle
import time
import logging
from typing import Optional

import ollama

from . import database

# ── 配置 ────────────────────────────────────────────────
EMBEDDING_MODEL = "bge-m3:latest"
OLLAMA_API_BASE = "http://localhost:11434/v1"
OLLAMA_EMBED_ENDPOINT = f"{OLLAMA_API_BASE}/embeddings"

logger = logging.getLogger(__name__)

# ── 缓存（进程内 + SQLite） ─────────────────────────────
# 进程内 LRU 缓存（避免每次查 SQLite）
_proc_cache: dict[str, list[float]] = {}


# ── Embedding ───────────────────────────────────────────

def get_embedding_cached(text: str) -> tuple[list[float], str]:
    """获取文本的 embedding（优先进程缓存 > SQLite 缓存 > Ollama）。

    Returns:
        (embedding, source): source in ("proc_cache", "sqlite_cache", "ollama")
    """
    text_hash = hashlib.sha256(text.encode()).hexdigest()

    # 1. 进程内缓存
    if text_hash in _proc_cache:
        return _proc_cache[text_hash], "proc_cache"

    # 2. SQLite 缓存
    blob = database.get_cached_embedding(text_hash)
    if blob is not None:
        emb = pickle.loads(blob)
        _proc_cache[text_hash] = emb
        return emb, "sqlite_cache"

    # 3. Ollama API
    emb = _call_ollama(text)

    # 写入两层缓存
    _proc_cache[text_hash] = emb
    database.set_cached_embedding(text_hash, pickle.dumps(emb))

    return emb, "ollama"


def _call_ollama(text: str, timeout: float = 30.0) -> list[float]:
    """调用 Ollama bge-m3 生成 embedding。"""
    try:
        resp = ollama.embeddings(
            model=EMBEDDING_MODEL,
            prompt=text[:512],  # bge-m3 建议 max 512 tokens
        )
        return resp["embedding"]
    except Exception as e:
        logger.error(f"Ollama embedding 失败: {e}")
        raise


def clear_proc_cache():
    """清空进程内缓存（通常在进程重启时调用）。"""
    global _proc_cache
    _proc_cache = {}


# ── 健康检查 ────────────────────────────────────────────

def is_ollama_healthy() -> dict:
    """检查 Ollama 服务和 bge-m3 模型是否可用。

    Returns:
        dict: {"healthy": bool, "model_installed": bool, "latency_ms": float, "error": str|None}
    """
    import requests

    result = {
        "healthy": False,
        "model_installed": False,
        "latency_ms": None,
        "error": None,
    }

    # 1. 检查服务
    try:
        t0 = time.time()
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        latency = (time.time() - t0) * 1000
        result["latency_ms"] = round(latency, 1)
        if r.status_code != 200:
            result["error"] = f"HTTP {r.status_code}"
            return result
    except Exception as e:
        result["error"] = f"连接失败: {e}"
        return result

    # 2. 检查模型
    try:
        models = [m["name"] for m in r.json().get("models", [])]
        if EMBEDDING_MODEL in models:
            result["model_installed"] = True
            result["healthy"] = True
        else:
            result["error"] = f"模型 {EMBEDDING_MODEL} 未安装"
    except Exception as e:
        result["error"] = f"解析响应失败: {e}"

    return result


def test_embedding() -> dict:
    """对已知文本进行 embedding，验证 pipeline 可用性。

    Returns:
        dict: {"success": bool, "dim": int, "sample": list, "latency_ms": float}
    """
    test_text = "Hermem Phase 2 使用 NumPy 向量库和 Ollama bge-m3 进行语义召回。"
    try:
        t0 = time.time()
        emb, src = get_embedding_cached(test_text)
        latency = (time.time() - t0) * 1000
        return {
            "success": True,
            "dim": len(emb),
            "sample": [round(x, 4) for x in emb[:5]],
            "latency_ms": round(latency, 1),
            "source": src,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
