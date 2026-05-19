#!/usr/bin/env python3
"""
Hermem Phase 3 - V4.3 B1: Disposition 联动更新器

功能：
  在 annotation 命中 prediction_errors 后，更新对应 l1_dispositions 的
  error_count / last_error_at / success_count。

匹配策略（优先级递减）：
  1. source_session_id 直接关联
  2. prediction_text 的 Jaccard 相似度匹配（阈值 0.5）

使用方式：
  from .disposition_updater import update_dispositions_from_errors
  updated = update_dispositions_from_errors(session_id, annotation)

集成位置：
  async_annotation.py 的 _worker() 中，annotate_l0_after_l1_v2() 返回后调用。
"""

import sqlite3
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DB_PATH
from .utils import db_query_dict


# ── 阈值常量 ────────────────────────────────────────────────────
JACCARD_THRESHOLD = 0.50   # 低于此值不匹配
MIN_PREDICTION_LEN = 5     # 预测文本太短不做 Jaccard 匹配


# ── 核心函数 ────────────────────────────────────────────────────

def update_dispositions_from_errors(
    session_id: str,
    annotation: dict[str, Any],
) -> int:
    """
    根据 L0 error_annotation 更新 l1_dispositions 的 error_count / last_error_at。

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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # ── 策略 1: source_session_id 直接关联 ────────────────────
    cursor.execute(
        "SELECT id, prediction_text FROM l1_dispositions "
        "WHERE source_session_id = ? AND is_active = 1",
        (session_id,)
    )
    direct_dispositions = cursor.fetchall()

    # ── 策略 2: 加载所有 active dispositions（用于 Jaccard 匹配）──
    # 仅在策略1无结果时做全局匹配，避免跨 session 错误扩散
    global_dispositions = []
    if not direct_dispositions:
        all_rows = db_query_dict(
            "SELECT id, prediction_text FROM l1_dispositions "
            "WHERE is_active = 1"
        )
        global_dispositions = all_rows

    now_iso = datetime.now().isoformat()
    updated_count = 0

    for error in prediction_errors:
        model_prediction = error.get("model_prediction", "")
        if not model_prediction or len(model_prediction) < MIN_PREDICTION_LEN:
            continue

        matched_id = None

        # 1. 优先直接关联
        for disp in direct_dispositions:
            pred_text = disp["prediction_text"] or ""
            if _jaccard_sim(model_prediction, pred_text) >= JACCARD_THRESHOLD:
                matched_id = disp["id"]
                break

        # 2. 无直接关联 → 全局 Jaccard 匹配（保守）
        if matched_id is None and not direct_dispositions:
            for disp in global_dispositions:
                pred_text = disp["prediction_text"] or ""
                if _jaccard_sim(model_prediction, pred_text) >= JACCARD_THRESHOLD:
                    matched_id = disp["id"]
                    break

        if matched_id is None:
            continue

        cursor.execute(
            "UPDATE l1_dispositions "
            "SET error_count = error_count + 1, "
            "    last_error_at = ? "
            "WHERE id = ?",
            (now_iso, matched_id)
        )
        updated_count += 1

    conn.commit()
    conn.close()
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

    now_iso = datetime.now().isoformat()
    cursor.execute(
        "UPDATE l1_dispositions "
        "SET success_count = success_count + 1 "
        "WHERE source_session_id = ? AND is_active = 1",
        (now_iso, session_id)
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


# ── 测试 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, sys

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
        print(f"  [{r['id'][:8]}] pred={pred_short!r}  errors={r['error_count']}  success={r['success_count']}  last_err={r['last_error_at']}")

    print("\n✓ 自测完成")
