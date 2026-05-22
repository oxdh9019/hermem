#!/usr/bin/env python3
"""
Hermem Phase 3 - L1 检索（Step 3b + 3c）
Step 3b: vector_search_l1() — 纯语义，无类型过滤
Step 3c: retrieve() — 后处理 boost（替代硬过滤）
"""

import queue
import re
import threading
from datetime import datetime as _dt

from .config import DB_PATH
from .utils import (
    cosine_sim,
    db_query_dict,
    deserialize_vec,
    get_embedding,
    json_loads,
)


# ── B8 公式（可测试的独立函数）────────────────────────────────────
def calculate_activation_score(
    sim: float,
    error_count: int,
    last_error_at: str | None,
    half_life_days: float = 7.0,
    max_error_count_cap: int = 5,
):
    """
    B8 三维权公式（简化版，无 DB 依赖）。

    score = sim × f_time × min(error_count, cap)

    cap 防止 error_count=100 的 disposition 垄断排序。
    error_count=0 → 0.5（抑制），error_count=1 → 1.0，2+ → cap 上限。

    f_time: 指数衰减，半衰期 half_life_days
    Returns: (score, f_time, error_factor)
    """
    if last_error_at:
        try:
            last_dt = _dt.fromisoformat(last_error_at.replace("Z", "+00:00"))
        except ValueError:
            f_time = 1.0
        else:
            now = _dt.now(last_dt.tzinfo) if last_dt.tzinfo else _dt.now()
            delta_days = (now - last_dt).total_seconds() / 86400.0
            f_time = 0.5 ** (delta_days / half_life_days)
    else:
        f_time = 1.0

    capped_error = min(error_count, max_error_count_cap)
    error_factor = 0.5 if capped_error == 0 else capped_error

    return sim * f_time * error_factor, f_time, error_factor


def disposition_aware_rerank(
    l1_results: list[dict],
    dispositions: list[dict],
    query: str = "",
    boost_factor: float = 1.5,
) -> list[dict]:
    """
    Phase 3 V4.5: Disposition-Aware Rerank。

    对 L1 检索结果做后处理 boost：与 top dispositions 共享 l0_ref 的 fact
    获得 boost_factor 倍分数。

    Boost 路径（优先级递减）：
      1. l0_ref 精确匹配（disposition.l0_ref == fact.l0_ref）
         注意：需要两边 ref 格式一致才生效（当前仅最近创建的 model_error dispositions）
      2. condition_text 关键词重叠（disposition 条件词命中 fact 内容）
         作为 fallback，覆盖 OpenClaw 导入的 user_behavior dispositions（UUID 格式无对应 L0）

    Args:
        l1_results:    vector_search_l1() 返回的原始结果（含 _sim 字段）
        dispositions:  vector_search_dispositions() 返回的结果（含 l0_ref 或 source_session_id）
        query:         原始查询（用于日志）
        boost_factor:  boost 倍数（默认 1.5）

    Returns:
        重新排序后的 l1_results（原地修改 _sim + _disposition_boost 标记）
    """
    if not l1_results or not dispositions:
        return l1_results

    # ── 路径1: l0_ref 精确匹配 ─────────────────────────────
    disp_l0_refs: set[str] = set()
    for d in dispositions:
        ref = d.get("l0_ref")
        if ref:
            disp_l0_refs.add(ref)

    # ── 路径2: condition 关键词 fallback ───────────────────
    # 收集所有 disposition condition 的关键词（用于 content 命中）
    condition_keywords: set[str] = set()
    for d in dispositions:
        cond = d.get("condition", "") or ""
        # 简单分词：中文按字符，英文按空格
        words = re.findall(r"[\u4e00-\u9fff]+", cond)
        for chunk in words:
            for i in range(len(chunk)):
                condition_keywords.add(chunk[i])
            for i in range(len(chunk) - 1):
                condition_keywords.add(chunk[i : i + 2])
        condition_keywords.update(w.lower() for w in re.findall(r"[a-zA-Z]+", cond))

    # 应用 boost
    boost_log: list[dict] = []
    for fact in l1_results:
        boosted = False
        match_method = None
        # 路径1: l0_ref 匹配
        if fact.get("l0_ref") in disp_l0_refs:
            fact["_sim"] = fact["_sim"] * boost_factor
            fact["_disposition_boost"] = True
            boosted = True
            match_method = "l0_ref"
        # 路径2: condition 关键词命中 fact content
        elif condition_keywords:
            content = fact.get("content", "") or ""
            content_lower = content.lower()
            hits = sum(1 for kw in condition_keywords if len(kw) > 1 and kw in content_lower)
            if hits >= 2:  # 至少命中 2 个关键词
                fact["_sim"] = fact["_sim"] * boost_factor
                fact["_disposition_boost"] = True
                boosted = True
                match_method = "keyword"
        if not boosted:
            fact["_disposition_boost"] = False
        # 记录日志（所有有 _disposition_boost=True 的 fact）
        if fact.get("_disposition_boost"):
            boost_log.append(
                {
                    "fact_id": fact["id"],
                    "fact_l0_ref": fact.get("l0_ref"),
                    "match_method": match_method,
                    "old_sim": round(fact["_sim"] / boost_factor, 4),
                    "new_sim": round(fact["_sim"], 4),
                }
            )

    # 重新按 _sim 降序排列
    l1_results.sort(key=lambda x: x["_sim"], reverse=True)

    # 写 boost 日志（异步，不阻塞返回）
    if boost_log:
        _write_boost_log(query, dispositions, boost_log)

    return l1_results


# ── Boost 日志写入（单线程队列模式）────────────────────────────────

_BOOST_LOG_PATH: str | None = None
_boost_queue: queue.Queue = queue.Queue(maxsize=1000)
_boost_writer_thread: threading.Thread | None = None


def _get_boost_log_path() -> str:
    """延迟解析日志路径，避免模块加载时 ~/.hermes 不可用"""
    global _BOOST_LOG_PATH
    if _BOOST_LOG_PATH is None:
        import os

        home = os.path.expanduser("~")
        log_dir = os.path.join(home, ".hermes", "logs")
        os.makedirs(log_dir, exist_ok=True)
        _BOOST_LOG_PATH = os.path.join(log_dir, "hermem-boost.jsonl")
    return _BOOST_LOG_PATH


def _ensure_boost_writer() -> None:
    """启动单个后台写线程（幂等）"""
    global _boost_writer_thread
    if _boost_writer_thread is None or not _boost_writer_thread.is_alive():
        _boost_writer_thread = threading.Thread(
            target=_boost_writer_loop, daemon=True, name="boost-writer"
        )
        _boost_writer_thread.start()


def _boost_writer_loop() -> None:
    """单线程消费 boost 日志队列"""
    while True:
        try:
            path, line = _boost_queue.get(timeout=5.0)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
            _boost_queue.task_done()
        except queue.Empty:
            continue
        except Exception:
            pass


def _write_boost_log(query: str, dispositions: list[dict], boost_entries: list[dict]) -> None:
    """将 boost 事件异步写入 JSONL 文件（队列模式，不阻塞返回）"""
    import json
    from datetime import datetime

    disp_ids = [d.get("id", "")[:40] for d in dispositions]
    entry = {
        "ts": datetime.now().isoformat(),
        "query": query,
        "disposition_ids": disp_ids,
        "boosted_facts": boost_entries,
    }
    try:
        path = _get_boost_log_path()
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        _ensure_boost_writer()
        try:
            _boost_queue.put_nowait((path, line))
        except queue.Full:
            pass  # 队列满则丢弃，不阻塞检索
    except Exception:
        pass  # 日志失败不影响主流程


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
        results.append(
            {
                "id": row["id"],
                "l0_ref": row["l0_ref"],
                "types": json_loads(row["types"]),
                "content": row["content"],
                "tags": json_loads(row["tags"]),
                "value": row["value"],
                "_sim": sim,
            }
        )

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

    from .config import (
        DISPOSITION_BASE_WEIGHT,
        DISPOSITION_HALF_LIFE_DAYS,
        DISPOSITION_MAX_ERROR_COUNT,
        DISPOSITION_MAX_FACTOR,
        DISPOSITION_MIN_COUNT,
    )
    from .disposition_updater import compute_disposition_weight

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if intent:
        rows = conn.execute(
            "SELECT id, l0_ref, source_session_id, condition_text, prediction_text, confidence, source_agent, "
            "       condition_embedding, intent, scope, "
            "       error_count, last_error_at, weight "
            "FROM l1_dispositions "
            "WHERE is_active = 1 AND intent = ?",
            (intent,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, l0_ref, source_session_id, condition_text, prediction_text, confidence, source_agent, "
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
        activation_score, f_time_ranking, error_factor = calculate_activation_score(
            sim=sim,
            error_count=error_count,
            last_error_at=row["last_error_at"],
            half_life_days=DISPOSITION_HALF_LIFE_DAYS,
            max_error_count_cap=DISPOSITION_MAX_ERROR_COUNT,
        )

        results.append(
            {
                "id": row["id"],
                "l0_ref": row["l0_ref"]
                if "l0_ref" in row.keys() and row["l0_ref"]
                else (row["source_session_id"] if "source_session_id" in row.keys() else None),
                "condition": row["condition_text"],
                "prediction": row["prediction_text"],
                "confidence": row["confidence"],
                "source_agent": row["source_agent"],
                "intent": row["intent"] if "intent" in row.keys() else None,
                "scope": row["scope"] if "scope" in row.keys() else None,
                "error_count": error_count,
                "weight": disp_weight,
                "_sim": sim,
                "_f_time": f_time_ranking,
                "_error_factor": error_factor,
                "_activation": activation_score,
            }
        )

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
    # V4.5: disposition-aware rerank — 与 top dispositions 共享 l0_ref 的 fact 获得 boost
    if dispositions:
        l1_results = disposition_aware_rerank(
            l1_results, dispositions, query=query, boost_factor=1.5
        )

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
        "facts": l1_results,
        "scenes": l2_scenes,
        "dispositions": dispositions,
        "query": query,
        "intent": intent,
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
            refs = (
                json_loads(scene["l1_refs"])
                if isinstance(scene["l1_refs"], str)
                else scene["l1_refs"]
            )
            if r["id"] in refs:
                matched_ids.add(scene["id"])

    return [s for s in all_scenes if s["id"] in matched_ids][:5]
