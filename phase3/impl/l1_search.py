#!/usr/bin/env python3
"""
Hermem Phase 3 - L1 检索（Step 3b + 3c）
Step 3b: vector_search_l1() — 纯语义，无类型过滤
Step 3c: retrieve() — 后处理 boost（替代硬过滤）
"""
import json as json_lib
from .config import DB_PATH
from .utils import (
    get_embedding, cosine_sim,
    deserialize_vec, json_loads, json_dumps,
    db_query_dict,
)


def vector_search_l1(query_emb, top_k: int = 20) -> list[dict]:
    """
    纯语义搜索 L1 facts。
    绝对不做任何 fact_type 过滤，只做向量相似度排序。
    """
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, l0_ref, types, content, tags, value, chunk_vector "
        "FROM l1_facts WHERE status = 'active'"
    ).fetchall()
    conn.close()

    q = query_emb
    results = []
    for row in rows:
        emb = deserialize_vec(row["chunk_vector"])
        sim = cosine_sim(q, emb)
        results.append({
            "id":       row["id"],
            "l0_ref":   row["l0_ref"],
            "types":    json_loads(row["types"]),
            "content":  row["content"],
            "tags":     json_loads(row["tags"]),
            "value":    row["value"],
            "_sim":     sim,
        })

    results.sort(key=lambda x: x["_sim"], reverse=True)
    return results[:top_k]


def retrieve(query: str, preferred_types: list[str] | None = None, top_k: int = 5) -> dict:
    """
    检索入口。

    原则：永远不做硬过滤。只做后处理 boost。

    参数:
        query:           用户查询
        preferred_types: 用户问题暗示的类型（用于 boost，不是过滤）
        top_k:           返回 fact 数量

    返回:
        {
            "facts":  [...],   # L1 检索结果（已 boost + 截断）
            "scenes": [...],  # 关联的 L2 scenes
            "query":  query,
        }
    """
    # Step 1: 纯语义搜索 top_k=20
    query_emb = get_embedding(query)
    l1_results = vector_search_l1(query_emb, top_k=20)

    # Step 2: 关联到 L2 scenes（用 scene_embedding 相似度）
    l2_scenes = _associate_scenes(l1_results)

    # Step 3: 后处理 boost（不是过滤！）
    if preferred_types:
        scored = []
        for r in l1_results:
            boost = 1.5 if any(t in r["types"] for t in preferred_types) else 1.0
            scored.append((r, r["_sim"] * boost))
        scored.sort(key=lambda x: x[1], reverse=True)
        l1_results = [r for r, _ in scored[:top_k]]
    else:
        l1_results = l1_results[:top_k]

    return {
        "facts":  l1_results,
        "scenes": l2_scenes,
        "query":  query,
    }


def _associate_scenes(l1_results: list[dict]) -> list[dict]:
    """
    将 L1 检索结果关联到 L2 scenes。
    基于共享的 l0_ref 和 tags 做宽松匹配。
    """
    if not l1_results:
        return []

    # 收集所有关联的 l0_ref
    l0_refs = list({r["l0_ref"] for r in l1_results})
    if not l0_refs:
        return []

    conn_sql = "SELECT * FROM l2_scenes WHERE status IN ('active','dormant')"
    all_scenes = db_query_dict(conn_sql)

    # 找与这些 L1 共享 l0_ref 的 scenes
    matched_ids = set()
    for r in l1_results:
        for scene in all_scenes:
            refs = json_loads(scene["l1_refs"]) if isinstance(scene["l1_refs"], str) else scene["l1_refs"]
            if r["id"] in refs:
                matched_ids.add(scene["id"])

    return [s for s in all_scenes if s["id"] in matched_ids][:5]
