#!/usr/bin/env python3
"""
Hermem Phase 3 - 共享工具函数
"""
import numpy as np
import requests
import struct
import json as _json
from .config import OLLAMA_URL, DB_PATH


# ── 向量操作 ────────────────────────────────────────────────
def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def serialize_vec(vec: list[float] | np.ndarray) -> bytes:
    arr = np.asarray(vec, dtype=np.float32)
    return arr.tobytes()


def deserialize_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


# ── Ollama Embedding ───────────────────────────────────────
def get_embedding(text: str, model: str = "bge-m3:latest") -> np.ndarray:
    """单条文本 embedding"""
    resp = requests.post(
        f"{OLLAMA_URL}/embeddings",
        json={"model": model, "input": text},
        timeout=60,
    )
    resp.raise_for_status()
    return np.array(resp.json()["data"][0]["embedding"], dtype=np.float32)


def get_embeddings_batch(texts: list[str], model: str = "bge-m3:latest") -> list[np.ndarray]:
    """批量文本 embedding（减少 HTTP 开销）"""
    if not texts:
        return []
    resp = requests.post(
        f"{OLLAMA_URL}/embeddings",
        json={"model": model, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    return [
        np.array(item["embedding"], dtype=np.float32)
        for item in sorted(resp.json()["data"], key=lambda x: x["index"])
    ]


# ── Ollama LLM ─────────────────────────────────────────────
def llm_generate(prompt: str, model: str = "qwen2.5:3b",
                 temperature: float = 0.3, max_tokens: int = 2048) -> str:
    """调用 Ollama chat API 生成文本，带超时和重试"""
    import urllib.request
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/chat/completions",
                data=json_dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json_loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == 0:
                print(f"  [llm] retry after error: {e}")
                continue
            raise


# ── JSON helpers ───────────────────────────────────────────
def json_dumps(obj, **kw) -> str:
    kw.setdefault("ensure_ascii", False)
    return _json.dumps(obj, **kw)


def json_loads(s: str):
    return _json.loads(s)


# ── DB helpers ─────────────────────────────────────────────
def db_execute(sql: str, params=()):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def db_query(sql: str, params=()) -> list[tuple]:
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def db_query_dict(sql: str, params=()) -> list[dict]:
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def call_minimax(prompt: str, model: str = "MiniMax-M2.7",
                  temperature: float = 0.3, max_tokens: int = 1024) -> str:
    """调用 MiniMax API（与 Hermes gateway 相同的认证方式）。

    从 ~/.hermes/.env 读取 MINIMAX_CN_API_KEY。
    支持 no_think: True 头部禁用思考模型。
    """
    import os
    api_key = None
    env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k in ("MINIMAX_CN_API_KEY", "MINIMAX_API_KEY"):
                    api_key = v.strip()
                    break
    if not api_key:
        raise RuntimeError("MINIMAX_CN_API_KEY not found in ~/.hermes/.env")

    import urllib.request
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                "https://api.minimaxi.com/anthropic/v1/messages",
                data=json_dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                    "x-no-think": "true",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json_loads(resp.read())
            content_blocks = data.get("content", [])
            for block in content_blocks:
                if block.get("type") == "text":
                    return block["text"]
            return ""
        except Exception as e:
            if attempt == 0:
                print(f"  [call_minimax] retry after error: {e}")
                continue
            raise
    return ""
