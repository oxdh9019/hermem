#!/usr/bin/env python3
"""
Hermem Phase 3 - L1 原子事实提取
Step 3a: extract_l1_facts() + store_l1_batch()
"""
import uuid
from datetime import datetime
from .config import DB_PATH, L1_EXTRACT_PROMPT
from .utils import get_embeddings_batch, llm_generate, serialize_vec, json_dumps, json_loads


def extract_l1_facts(session_summary: str) -> list[dict]:
    """
    调用 LLM 从会话摘要中提取 L1 facts。
    返回 facts 列表，每条含 types/content/tags/value。
    """
    prompt = L1_EXTRACT_PROMPT.format(SESSION_SUMMARY=session_summary)
    content = llm_generate(prompt, temperature=0.3, max_tokens=2048)

    # 解析 JSON（可能被 markdown 包裹）
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip().strip("`")

    data = json_loads(text)
    # LLM 可能返回 {"facts": [...]} 或直接是 [...]
    if isinstance(data, list):
        facts = data
    else:
        facts = data.get("facts", [])
    valid = []
    for f in facts:
        if not f.get("content") or f.get("value") not in ("high", "medium"):
            continue
        raw_types = f.get("types", ["other"])
        # 递归展开任意深度的嵌套列表
        def _flat(t):
            if not isinstance(t, list):
                return [t]
            result = []
            for x in t:
                result.extend(_flat(x))
            return result
        types = _flat(raw_types)
        types = [t for t in types if t in ("decision", "bug-fix", "preference", "method", "todo", "unresolved", "other")]
        if not types:
            types = ["other"]
        valid.append({**f, "types": types})

    return valid


def store_l1_batch(facts: list[dict], l0_ref: str) -> list[str]:
    """
    将 L1 facts 批量写入数据库（同时生成 embedding）。
    返回写入的 fact_id 列表。
    """
    if not facts:
        return []

    import sqlite3
    from .utils import get_embedding

    texts = [f["content"] for f in facts]
    embeddings = get_embeddings_batch(texts)  # 批量 embedding

    now = datetime.now().isoformat()
    ids = []

    conn = sqlite3.connect(DB_PATH)
    for fact, emb in zip(facts, embeddings):
        fid = f"fact_{uuid.uuid4().hex[:8]}"
        ids.append(fid)
        conn.execute("""
            INSERT INTO l1_facts
            (id, l0_ref, types, type_confidence, fallback_type,
             content, tags, value, chunk_vector, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """, (
            fid,
            l0_ref,
            json_dumps(fact.get("types", ["other"])),
            fact.get("type_confidence", 1.0),
            fact.get("fallback_type", "other"),
            fact["content"],
            json_dumps(fact.get("tags", [])),
            fact.get("value", "medium"),
            serialize_vec(emb.tolist()),
            now,
        ))
    conn.commit()
    conn.close()
    return ids
