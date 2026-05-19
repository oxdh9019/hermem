#!/usr/bin/env python3
"""
Hermem Phase 3 - L1 检索（Step 3b + 3c）
Step 3b: vector_search_l1() — 纯语义，无类型过滤
Step 3c: retrieve() — 后处理 boost（替代硬过滤）
"""
import json as json_lib
from datetime import datetime
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


def vector_search_dispositions(
    query_emb,
    top_k: int = 5,
    intent: str | None = None,
) -> list[dict]:
    """
    语义搜索 l1_dispositions 表的 condition_text。
    与 vector_search_l1 并行工作，但针对行为模式检索。

    B6: activation_score = semantic_similarity * disposition_weight
         weight = base * f_time * f_freq（时间衰减 × 频次增强）

    Args:
        query_emb:       查询向量
        top_k:           返回数量
        intent:          可选，按 intent 过滤（13种意图之一）。
                         为 None 时不做 intent 过滤。
    """
    import sqlite3
    from .disposition_updater import compute_disposition_weight
    from .config import (
        DISPOSITION_HALF_LIFE_DAYS,
        DISPOSITION_MIN_COUNT,
        DISPOSITION_MAX_FACTOR,
        DISPOSITION_BASE_WEIGHT,
        DISPOSITION_MAX_ERROR_COUNT,
    )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if intent:
        rows = conn.execute(
            "SELECT id, condition_text, prediction_text, confidence, source_agent, "
            "       condition_embedding, intent, scope, "
            "       error_count, last_error_at, weight "
            "FROM l1_dispositions "
            "WHERE is_active = 1 AND intent = ?",
            (intent,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, condition_text, prediction_text, confidence, source_agent, "
            "       condition_embedding, intent, scope, "
            "       error_count, last_error_at, weight "
            "FROM l1_dispositions WHERE is_active = 1 AND scope = 'model_error'"
        ).fetchall()
    conn.close()

    results = []
    for row in rows:
        emb_bytes = row["condition_embedding"]
        if not emb_bytes:
            continue
        emb = deserialize_vec(emb_bytes)
        sim = cosine_sim(query_emb, emb)

        # ── B6: 计算激活权重 ──
        # 优先用预存的 weight，fallback 到实时计算
        stored_weight = row["weight"]
        if stored_weight is not None:
            disp_weight = stored_weight
        else:
            disp_weight = compute_disposition_weight(
                last_error_at=row["last_error_at"],
                error_count=row["error_count"] or 0,
                half_life_days=DISPOSITION_HALF_LIFE_DAYS,
                min_count=DISPOSITION_MIN_COUNT,
                max_factor=DISPOSITION_MAX_FACTOR,
                base_weight=DISPOSITION_BASE_WEIGHT,
            )

        # ── B8: activation_score = sim × f_time × min(error_count, cap) ──
        # 与 B6 的 f_freq 解耦：B8 让 error_count 直接参与排序，
        # cap 防止单个 disposition 垄断（如 error_count=100 的 disposition）
        error_count = row["error_count"] or 0
        capped_error = min(error_count, DISPOSITION_MAX_ERROR_COUNT)
        # f_time 仍然决定时间衰减
        if row["last_error_at"]:
            try:
                last_dt = datetime.fromisoformat(row["last_error_at"].replace("Z", "+00:00"))
            except ValueError:
                f_time_ranking = 1.0
            else:
                now = datetime.now(last_dt.tzinfo) if last_dt.tzinfo else datetime.now()
                delta_days = (now - last_dt).total_seconds() / 86400.0
                f_time_ranking = 0.5 ** (delta_days / DISPOSITION_HALF_LIFE_DAYS)
        else:
            f_time_ranking = 1.0

        # error_count=0 → 0.5（抑制），error_count=1 → 1.0，2+ → 线性增长 capped
        if capped_error == 0:
            error_factor = 0.5
        else:
            error_factor = capped_error

        activation_score = sim * f_time_ranking * error_factor

        results.append({
            "id":             row["id"],
            "condition":      row["condition_text"],
            "prediction":     row["prediction_text"],
            "confidence":     row["confidence"],
            "source_agent":   row["source_agent"],
            "intent":         row["intent"] if "intent" in row.keys() else None,
            "scope":          row["scope"] if "scope" in row.keys() else None,
            "error_count":    error_count,
            "weight":         disp_weight,
            "_sim":           sim,
            "_f_time":        f_time_ranking,
            "_error_factor":  error_factor,
            "_activation":    activation_score,
        })

    # B6: 按 activation_score 排序，而非原始相似度
    results.sort(key=lambda x: x["_activation"], reverse=True)
    return results[:top_k]


def retrieve(
    query: str,
    preferred_types: list[str] | None = None,
    intent: str | None = None,
    top_k: int = 5,
    disposition_k: int = 3,
) -> dict:
    """
    检索入口。

    原则：永远不做硬过滤。只做后处理 boost。

    参数:
        query:            用户查询
        preferred_types:  用户问题暗示的类型（用于 boost，不是过滤）
        intent:           意图分类结果（13种之一，或 None）
                          intent 为 "other" 时询问用户，不继续
                          intent 为 None 时不做 intent 过滤
        top_k:            返回 fact 数量
        disposition_k:     返回 disposition 数量（默认 3）

    返回:
        {
            "facts":        [...],   # L1 检索结果（已 boost + 截断）
            "scenes":       [...],   # 关联的 L2 scenes
            "dispositions": [...],   # 条件-预测行为模式
            "query":        query,
            "intent":      intent,  # 本次使用的意图（None 表示未分类）
        }
    """
    # Step 1: 纯语义搜索 top_k=20
    query_emb = get_embedding(query)

    # Step 1b: 并行搜索 dispositions（intent 过滤）
    dispositions = vector_search_dispositions(
        query_emb,
        top_k=disposition_k,
        intent=intent if intent and intent != "other" else None,
    )

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
        "facts":        l1_results,
        "scenes":       l2_scenes,
        "dispositions": dispositions,
        "query":        query,
        "intent":       intent,
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
