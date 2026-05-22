#!/usr/bin/env python3
"""
Hermem Phase 3 - 共享工具函数
"""

import json as _json
from pathlib import Path

import numpy as np
import requests

from .config import DB_PATH, OLLAMA_URL


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


# ── Ollama LLM (native /api/chat) ─────────────────────────
def llm_generate_ollama(
    prompt: str,
    model: str = "qwen3.5:4b-no-think",
    temperature: float = 0.1,
    max_tokens: int = 50,
) -> str:
    """调用 Ollama 原生 /api/chat 接口（不走 OpenAI 兼容层）。

    适用于 qwen3.5:4b-no-think 等需要直接 Ollama API 的模型。
    注意：OLLAMA_URL 含 /v1 前缀，/api/chat 需要去掉 /v1。
    """
    import requests as _requests

    base_url = OLLAMA_URL.replace("/v1", "")  # strip /v1 suffix for native API
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    resp = _requests.post(
        f"{base_url}/api/chat",
        json=payload,
        timeout=180,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    content = resp.json().get("message", {}).get("content", "")
    return content.strip()


# ── Ollama LLM (OpenAI-compatible /chat/completions) ──────
def llm_generate(
    prompt: str,
    model: str = "qwen3.5:4b-no-think",
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    """调用 LLM（Ollama 或 MiniMax），根据 model 名称自动路由"""
    # MiniMax 路由（支持 MiniMax-M2.7、MiniMax-M2 等）
    if model and "MiniMax" in model:
        return _call_minimax(prompt, model=model, temperature=temperature, max_tokens=max_tokens)

    # Ollama 默认路径
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


def _call_minimax(
    prompt: str,
    model: str = "MiniMax-M2.7",
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    """调用 MiniMax Chat API（Anthropic 兼容格式）"""
    import re
    import urllib.request

    _AUTH_PATH = Path.home() / ".hermes" / "auth.json"
    if not _AUTH_PATH.exists():
        raise RuntimeError(f"auth.json not found at {_AUTH_PATH}")
    _cred = json_loads(_AUTH_PATH.read_text())
    creds = _cred["credential_pool"]["minimax-cn"][0]
    api_key = creds["access_token"]
    base_url = "https://api.minimaxi.com/anthropic"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "no_think": True,
    }
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                base_url + "/v1/messages",
                data=json_dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json_loads(resp.read())
            content = data.get("content", [])
            raw = next((c["text"] for c in content if c.get("type") == "text"), "")
            # Remove markdown code block wrappers
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"^\s*```", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return raw.strip()
        except Exception as e:
            if attempt == 0:
                print(f"  [minimax] retry after error: {e}")
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
