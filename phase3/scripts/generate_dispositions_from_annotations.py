#!/usr/bin/env python3
"""
从已有的 L0 error_annotation 生成真正的 disposition（基于模型错误模式）。

用途：V4.3 Step 2 — 用真实 annotation 数据种子第一批语义对齐的 dispositions，
      替代 OpenClaw 导入的 Oliver 行为描述。

运行：
    cd ~/.hermes/projects/hermem-github/phase3
    python3 -m scripts.generate_dispositions_from_annotations

输出：
    向 l1_dispositions 表插入新记录的 disposition，
    scope='model_error'，source_agent='hermem_annotation_seed'。
"""

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# 添加 phase3 到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from impl.config import DB_PATH, L0_DIR, OLLAMA_URL, ERROR_ANNOTATION_MODEL
from impl.utils import llm_generate, get_embedding


PROMPT_TEMPLATE = """你是一个错误模式分析专家。基于以下对话中的预测误差（error_annotation），
生成一个可用于记忆系统的 disposition（条件-预测对）。

误差信息：
- 模型预测（model_prediction）：{model_prediction}
- 实际结果（actual_outcome）：{actual_outcome}
- 错误类型（error_type）：{error_type}

请输出一个 JSON 对象，包含以下字段：
{{
  "condition_text": "触发该错误的条件描述（以 When 或 If 开头）",
  "prediction_text": "模型当时做出的错误预测",
  "error_type": "{error_type}",
  "keywords": "关键词1,关键词2,关键词3",
  "confidence": 0.8
}}

要求：
- condition_text 应描述在什么样的情况下模型容易犯此类错误。
- 不要捏造，只基于给定信息合理概括。
- 输出必须是合法 JSON，不要包含其他文字。
"""


def generate_disposition(annotation: dict) -> dict | None:
    """调用 LLM 将单条 annotation 转换为 disposition。"""
    model_pred = annotation.get("model_prediction", "") or ""
    actual = annotation.get("actual_outcome", "") or ""
    error_type = annotation.get("error_type", "other") or "other"

    if not model_pred:
        return None

    prompt = PROMPT_TEMPLATE.format(
        model_prediction=model_pred,
        actual_outcome=actual,
        error_type=error_type,
    )

    try:
        response = llm_generate(prompt, model=ERROR_ANNOTATION_MODEL, temperature=0.3)
    except Exception as e:
        print(f"  [LLM 调用失败] {e}")
        return None

    # 提取 JSON
    text = response.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else parts[0]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().strip("`")

    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        print(f"  [JSON 解析失败] 原始响应: {text[:100]}")
        return None

    try:
        disp = json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"  [JSON 解析失败] {e}  文本: {text[:100]}")
        return None

    # 补充元信息
    disp["source_session_id"] = annotation.get("source_session_id", "unknown")
    disp["source_agent"] = "hermem_annotation_seed"
    disp["is_active"] = 1
    disp["scope"] = "model_error"

    return disp


def main():
    l0_dir = Path(L0_DIR)

    # 收集所有带 error_annotation 的 L0 文件
    annotations = []
    for l0_path in sorted(l0_dir.glob("*.json")):
        data = json.loads(l0_path.read_text())
        ann = data.get("error_annotation", {})
        errors = ann.get("prediction_errors", [])
        if errors:
            for err in list(errors):  # shallow copy
                err = dict(err)
                err["source_session_id"] = data["session_id"]
                annotations.append(err)

    if not annotations:
        print("未找到任何 error_annotation，退出。")
        return

    print(f"找到 {len(annotations)} 条 annotation，开始生成 dispositions...\n")

    db_path = Path(DB_PATH)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    added = 0
    for ann in annotations:
        model_pred = ann.get("model_prediction", "")[:50]
        print(f"\n处理: [{ann.get('error_type')}] {model_pred}")

        disp = generate_disposition(ann)
        if disp is None:
            print("  → 跳过（生成失败）")
            continue

        try:
            new_id = f"disp_hm_{datetime.now().strftime('%Y%m%d%H%M%S')}_{cursor.lastrowid}"
            now = datetime.now().isoformat()
            cursor.execute("""
                INSERT INTO l1_dispositions
                    (id, condition_text, prediction_text, error_type, keywords,
                     confidence, source_session_id, source_agent, is_active,
                     scope, created_at, condition_embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_id,
                disp.get("condition_text", ""),
                disp.get("prediction_text", ""),
                disp.get("error_type", "other"),
                disp.get("keywords", ""),
                disp.get("confidence", 0.8),
                disp["source_session_id"],
                disp["source_agent"],
                disp["is_active"],
                disp.get("scope", "model_error"),
                now,
                None,  # condition_embedding filled below
            ))
            # Generate embedding for condition_text
            cond_text = disp.get("condition_text", "")
            if cond_text:
                emb = get_embedding(cond_text)
                emb_bytes = np.array(emb, dtype=np.float32).tobytes()
                cursor.execute(
                    "UPDATE l1_dispositions SET condition_embedding=? WHERE id=?",
                    (emb_bytes, new_id)
                )
            added += 1
            print(f"  → 新增: {disp.get('condition_text', '')[:60]}")
        except Exception as e:
            print(f"  → 插入失败: {e}")

    conn.commit()

    # 验证
    print(f"\n插入完成，共 {added} 条新 disposition。")
    print("\n现有 dispositions（scope='model_error'）:")
    rows = cursor.execute(
        "SELECT id, error_type, keywords, scope FROM l1_dispositions "
        "WHERE scope = 'model_error' AND is_active = 1"
    ).fetchall()
    for r in rows:
        print(f"  [{r['id'][:40]}] type={r['error_type']}  kw={r['keywords']}")

    conn.close()


if __name__ == "__main__":
    main()
