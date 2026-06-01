#!/usr/bin/env python3
"""
Hermem Phase 3 - V4.3 B1: Disposition 联动更新器（三层 cascade 匹配）

功能：
  在 annotation 命中 prediction_errors 后，更新对应 l1_dispositions 的
  error_count / last_error_at / success_count。

匹配策略（优先级递减）：
  Layer 1: error_type 精确匹配
  Layer 2: 关键词交集匹配（≥2 个关键词命中）
  Layer 3: condition_embedding 余弦相似度兜底（阈值 0.45）

使用方式：
  from .disposition_updater import update_dispositions_from_errors
  updated = update_dispositions_from_errors(session_id, annotation)

集成位置：
  async_annotation.py 的 _worker() 中，annotate_l0_after_l1_v2() 返回后调用。
"""

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DB_PATH

# ── 阈值常量 ────────────────────────────────────────────────────
EMBEDDING_THRESHOLD = 0.40  # Layer 3 fallback 阈值（0.40 以下相似度与随机无异）
KEYWORD_MIN_OVERLAP = 2  # Layer 2 至少命中 2 个关键词


# ── 工具函数 ────────────────────────────────────────────────────


def extract_keywords(text: str) -> set[str]:
    """
    从文本中提取关键词（中文按字符/bigram，英文按分词）。
    去除常见停用词，返回小写集合。
    """
    if not text:
        return set()

    stopwords = {
        "的",
        "了",
        "是",
        "在",
        "我",
        "你",
        "他",
        "她",
        "它",
        "我们",
        "你们",
        "他们",
        "这",
        "那",
        "有",
        "说",
        "也",
        "不",
        "就",
        "都",
        "啊",
        "呢",
        "吧",
        "吗",
        "哦",
        "和",
        "与",
        "或",
        "但",
        "而",
        "着",
        "过",
        "被",
        "把",
        "给",
        "让",
        "向",
        "从",
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "what",
        "which",
        "that",
        "this",
    }

    # 中文 bigram + unigram
    chinese_chars = re.findall(r"[\u4e00-\u9fff]+", text)
    chinese_words = set()
    for chunk in chinese_chars:
        for i in range(len(chunk)):
            if len(chunk[i]) > 0:
                chinese_words.add(chunk[i].lower())
        for i in range(len(chunk) - 1):
            chinese_words.add(chunk[i : i + 2].lower())

    # 英文分词
    english_words = {w.lower() for w in re.findall(r"[a-zA-Z]+", text)}

    all_words = chinese_words | english_words
    return {w for w in all_words if w not in stopwords and len(w) > 1}


def _update_error_count(db_path: Path, disp_id: str, now_iso: str) -> None:
    """更新单条 disposition 的 error_count，返回是否实际更新（error_count>0 时才写）"""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE l1_dispositions "
        "SET error_count = error_count + 1, "
        "    last_error_at = ? "
        "WHERE id = ?",
        (now_iso, disp_id),
    )
    conn.commit()
    conn.close()


def _record_prediction_error_v55(error_type: str, context: str, model_pred: str = "") -> None:
    """V5.5: 写入 hermem.db.prediction_errors（供 L4 反思层使用）。

    即使未匹配到任何 disposition 也记录——L4 关心错误模式本身。
    失败不抛异常，不阻塞 disposition 更新链路。
    """
    hermem_db = Path.home() / ".hermes" / "memory" / "hermem.db"
    full_context = f"{model_pred} | {context}".strip(" |") if (model_pred or context) else "(empty)"
    try:
        conn = sqlite3.connect(str(hermem_db))
        try:
            conn.execute(
                "INSERT INTO prediction_errors (context, error_type, surprise_level) "
                "VALUES (?, ?, ?)",
                (full_context[:500], error_type or "unknown", 0.5),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"  [V5.5] prediction_errors 写库失败（不影响 disposition 更新）: {e}")


# ── 核心函数 ────────────────────────────────────────────────────


def update_dispositions_from_errors(
    session_id: str,
    annotation: dict[str, Any],
) -> int:
    """
    根据 L0 error_annotation 更新 l1_dispositions 的 error_count / last_error_at。

    三层 cascade 匹配（优先级递减）：
      Layer 1: error_type 精确匹配（需 disposition.error_type 非空）
      Layer 2: 关键词交集匹配（提取 annotation 的 model_prediction 关键词，
               与 disposition.keywords 或 condition_text/prediction_text 匹配，
               至少 2 个交集）
      Layer 3: condition_embedding 余弦相似度兜底（阈值 0.45）

    Args:
        session_id:  当前会话 ID
        annotation:  annotate_l0_after_l1_v2() 返回的 annotation dict，
                     含 prediction_errors 列表

    Returns:
        实际更新的 disposition 数量
    """
    prediction_errors = annotation.get("prediction_errors", [])
    if not prediction_errors:
        return 0

    db_path = Path(DB_PATH).expanduser()

    # 加载所有 active dispositions（仅 scope='model_error'）
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    dispositions = conn.execute(
        "SELECT id, condition_text, prediction_text, condition_embedding, "
        "       error_type, keywords "
        "FROM l1_dispositions "
        "WHERE is_active = 1 AND scope = 'model_error'"
    ).fetchall()
    conn.close()

    if not dispositions:
        return 0

    # 预加载 condition_embedding 为 numpy 数组
    from .utils import cosine_sim, deserialize_vec, get_embedding

    try:
        import numpy as _np
    except ImportError:
        return 0

    disp_vecs = {}
    for d in dispositions:
        emb_bytes = d["condition_embedding"]
        if emb_bytes:
            try:
                disp_vecs[d["id"]] = _np.array(deserialize_vec(emb_bytes), dtype=_np.float64)
            except Exception:
                disp_vecs[d["id"]] = None
        else:
            disp_vecs[d["id"]] = None

    now_iso = datetime.now().isoformat()
    updated_count = 0

    for error in prediction_errors:
        error_type = error.get("error_type", "") or ""
        model_pred = error.get("model_prediction", "") or ""
        context = error.get("context", "") or ""

        # V5.5: 写入 hermem.db.prediction_errors（无论是否匹配 disposition）
        _record_prediction_error_v55(error_type, context, model_pred)

        if not model_pred.strip():
            continue

        # ── Layer 1: error_type 精确匹配 ──
        matched_id = None
        match_reason = ""

        if error_type:
            for d in dispositions:
                if d["error_type"] and d["error_type"] == error_type:
                    matched_id = d["id"]
                    match_reason = f"error_type={error_type}"
                    _update_error_count(db_path, matched_id, now_iso)
                    updated_count += 1
                    print(
                        f"  [L1 match] {match_reason}  disp={matched_id[:40]}  pred={model_pred[:40]}"
                    )
                    break

        # ── Layer 2: 关键词交集匹配 ──
        if matched_id is None:
            error_kw = extract_keywords(model_pred)
            if context:
                error_kw |= extract_keywords(context)

            if error_kw:
                best_kw_match = None
                best_overlap = 0

                for d in dispositions:
                    # 优先用 disposition.keywords，其次 condition_text + prediction_text
                    disp_keywords_raw = d["keywords"] or ""
                    if disp_keywords_raw:
                        disp_kw = {
                            k.strip().lower() for k in disp_keywords_raw.split(",") if k.strip()
                        }
                    else:
                        disp_kw = extract_keywords(
                            (d["condition_text"] or "") + " " + (d["prediction_text"] or "")
                        )

                    overlap = len(error_kw & disp_kw)
                    if overlap >= KEYWORD_MIN_OVERLAP and overlap > best_overlap:
                        best_overlap = overlap
                        best_kw_match = d["id"]

                if best_kw_match:
                    matched_id = best_kw_match
                    match_reason = f"keywords_overlap={best_overlap}"
                    _update_error_count(db_path, matched_id, now_iso)
                    updated_count += 1
                    print(
                        f"  [L2 match] {match_reason}  disp={matched_id[:40]}  pred={model_pred[:40]}"
                    )

        # ── Layer 3: embedding 语义相似度 fallback ──
        if matched_id is None:
            try:
                query_emb = get_embedding(model_pred)
                query_vec = _np.array(query_emb, dtype=_np.float64)
            except Exception:
                query_vec = None

            if query_vec is not None:
                best_sim = -1.0
                best_emb_id = None

                for d in dispositions:
                    d_vec = disp_vecs.get(d["id"])
                    if d_vec is None:
                        continue
                    sim = float(cosine_sim(query_vec, d_vec))
                    if sim > best_sim:
                        best_sim = sim
                        best_emb_id = d["id"]

                if best_emb_id is not None and best_sim > EMBEDDING_THRESHOLD:
                    matched_id = best_emb_id
                    match_reason = f"embedding_sim={best_sim:.3f}"
                    _update_error_count(db_path, matched_id, now_iso)
                    updated_count += 1
                    print(
                        f"  [L3 match] {match_reason}  disp={matched_id[:40]}  pred={model_pred[:40]}"
                    )

    return updated_count


def increment_success_count(session_id: str) -> int:
    """
    当一次对话无任何 prediction_error 时，累加 success_count。
    计入 source_session_id 直接关联的所有 dispositions。

    这使得 error_rate = error_count / (error_count + success_count) 成为可能。
    """
    db_path = Path(DB_PATH).expanduser()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    datetime.now().isoformat()
    cursor.execute(
        "UPDATE l1_dispositions "
        "SET success_count = success_count + 1 "
        "WHERE source_session_id = ? AND is_active = 1",
        (session_id,),
    )
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    return updated


def increment_success_by_ids(disposition_ids: list[str], session_id: str) -> int:
    """
    V4.4 验证段: 按 disposition ID 列表累加 success_count。

    在 Turn N+1 判断 Turn N 激活的 disposition 预测被满足后调用。
    避免 session_id 匹配过于宽泛的问题（一个 session 可能关联多条 disposition）。

    Args:
        disposition_ids: 要更新的 disposition id 列表
        session_id: 来源 session（用于日志和 debugging）

    Returns:
        实际更新的 disposition 数量
    """
    if not disposition_ids:
        return 0

    db_path = Path(DB_PATH).expanduser()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now_iso = datetime.now().isoformat()
    placeholders = ",".join(["?"] * len(disposition_ids))
    cursor.execute(
        f"UPDATE l1_dispositions "
        f"SET success_count = success_count + 1, "
        f"last_used_at = ? "
        f"WHERE id IN ({placeholders}) AND is_active = 1",
        [now_iso] + disposition_ids,
    )
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    return updated


# ── 工具函数 ────────────────────────────────────────────────────


def _jaccard_sim(text1: str, text2: str) -> float:
    """
    混合 Jaccard 相似度：
    - 英文/数字：按空格分词
    - 中文/混合：按字符级重叠计算
    结果取两者较高者。
    """
    if not text1 or not text2:
        return 0.0

    # 英文分词 Jaccard
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    word_inter = len(words1 & words2)
    word_union = len(words1 | words2)
    word_sim = word_inter / word_union if word_union > 0 else 0.0

    # 中文字符级 Jaccard（适用于连续字符无空格文本）
    chars1 = set(text1)
    chars2 = set(text2)
    char_inter = len(chars1 & chars2)
    char_union = len(chars1 | chars2)
    char_sim = char_inter / char_union if char_union > 0 else 0.0

    return max(word_sim, char_sim)


# ── B6: Disposition 衰减机制 ──────────────────────────────────


def compute_disposition_weight(
    last_error_at: str | None,
    error_count: int,
    half_life_days: float = 7.0,
    min_count: int = 2,
    max_factor: float = 2.0,
    base_weight: float = 1.0,
) -> float:
    """
    计算 disposition 的激活权重。

    公式：weight = base_weight * f_time * f_freq

    f_time:  时间衰减因子，指数衰减，半衰期 half_life_days
            从未触发（last_error_at IS NULL）→ f_time = 1.0
    f_freq:  频次增强因子，error_count 越多越高
            error_count=0 → 0.5（抑制）
            error_count=1 → 1.0（中性）
            error_count>=min_count → 逐步增强，上限 max_factor
    """
    # ── f_time: 时间衰减 ──
    if last_error_at is None:
        f_time = 1.0
    else:
        try:
            last_dt = datetime.fromisoformat(last_error_at.replace("Z", "+00:00"))
        except ValueError:
            f_time = 1.0
        else:
            now = datetime.now(last_dt.tzinfo) if last_dt.tzinfo else datetime.now()
            delta_days = (now - last_dt).total_seconds() / 86400.0
            f_time = 0.5 ** (delta_days / half_life_days)

    # ── f_freq: 频次增强 ──
    if error_count == 0:
        f_freq = 0.5
    elif error_count == 1:
        f_freq = 1.0
    else:
        f_freq = min(max_factor, 1.0 + (error_count - 1) * 0.2)

    return base_weight * f_time * f_freq


def update_disposition_weights(
    half_life_days: float | None = None,
) -> dict:
    """
    批量重新计算所有 active disposition 的权重，写入 l1_dispositions.weight。
    用于定期 decay run 或 Gateway 启动时初始化。

    Returns: {"updated": N, "weights": {id: weight, ...}}
    """
    from .config import (
        DISPOSITION_BASE_WEIGHT,
        DISPOSITION_HALF_LIFE_DAYS,
        DISPOSITION_MAX_FACTOR,
        DISPOSITION_MIN_COUNT,
    )

    half_life = half_life_days or DISPOSITION_HALF_LIFE_DAYS

    db_path = Path(DB_PATH).expanduser()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id, last_error_at, error_count FROM l1_dispositions WHERE is_active = 1")
    rows = cursor.fetchall()

    results = {}
    updated = 0

    for row in rows:
        w = compute_disposition_weight(
            last_error_at=row["last_error_at"],
            error_count=row["error_count"],
            half_life_days=half_life,
            min_count=DISPOSITION_MIN_COUNT,
            max_factor=DISPOSITION_MAX_FACTOR,
            base_weight=DISPOSITION_BASE_WEIGHT,
        )
        cursor.execute(
            "UPDATE l1_dispositions SET weight = ? WHERE id = ?",
            (w, row["id"]),
        )
        if cursor.rowcount > 0:
            updated += 1
        results[row["id"]] = round(w, 4)

    conn.commit()
    conn.close()
    return {"updated": updated, "weights": results}


# ── 测试 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    print("=== Disposition Updater 自测 ===\n")

    # 加载一个真实 L0 文件测试
    L0_DIR = Path.home() / ".hermes" / "memory" / "l0_raw"
    l0_files = sorted(L0_DIR.glob("*.json"))
    if not l0_files:
        print("⚠ 没有找到 L0 文件，跳过自测")
        sys.exit(0)

    # 找一个有 error_annotation 的 L0
    found = False
    for lf in l0_files:
        data = json.loads(lf.read_text())
        if "error_annotation" in data and data["error_annotation"].get("prediction_errors"):
            ann = data["error_annotation"]
            print(f"测试文件: {lf.name}")
            print(f"  prediction_errors: {len(ann['prediction_errors'])} 条")
            for e in ann["prediction_errors"]:
                print(f"    - [{e.get('error_type')}] {e.get('model_prediction', '')[:60]}")

            updated = update_dispositions_from_errors(lf.stem, ann)
            print(f"\n  update_dispositions_from_errors → 更新了 {updated} 条 disposition")
            found = True
            break

    if not found:
        print("⚠ 没有找到带 prediction_errors 的 L0 文件，跳过")
        sys.exit(0)

    # 验证 DB 结果
    print("\n=== 更新后 l1_dispositions 状态 ===")
    from .utils import db_query_dict

    rows = db_query_dict(
        "SELECT id, prediction_text, error_count, success_count, last_error_at "
        "FROM l1_dispositions WHERE is_active = 1"
    )
    for r in rows:
        pred_short = (r["prediction_text"] or "")[:40]
        print(
            f"  [{r['id'][:8]}] pred={pred_short!r}  errors={r['error_count']}  success={r['success_count']}  last_err={r['last_error_at']}"
        )

    print("\n✓ 自测完成")
