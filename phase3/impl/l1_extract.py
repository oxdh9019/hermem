#!/usr/bin/env python3
"""
Hermem Phase 3 - L1 原子事实提取
Step 3a: extract_l1_facts() + store_l1_batch()
"""

import uuid
from datetime import datetime

from .config import DB_PATH, L1_EXTRACT_PROMPT
from .utils import (
    get_embeddings_batch,
    json_dumps,
    json_loads,
    llm_generate,
    serialize_vec,
)


def _try_fix_truncated_json(text: str):
    """
    尝试修复 LLM 返回的截断 JSON。
    LLM 输出常因 max_tokens 限制而在中间断开，常见模式：
      - {"facts": [...]  →  缺 }]
      - [{"a": "b", ...  →  缺 ]}
    """
    import re as _re

    # 统计括号深度
    opens = text.count("{") + text.count("[")
    closes = text.count("}") + text.count("]")
    if opens <= closes:
        return None

    # 尝试追加缺失的闭合符
    opens - closes
    # 从末尾向前找，统计未匹配的 { 和 [
    stack = []
    for m in _re.finditer(r"[\{\[\}\]]", text):
        c = m.group()
        if c in "{[":
            stack.append(c)
        elif c in "}]":
            expected = "{" if c == "}" else "["
            if stack and stack[-1] == expected:
                stack.pop()
            else:
                stack.append(c)  # 不匹配，压入

    if not stack:
        return None

    # 补充缺失的闭合括号（逆序出栈）
    suffix = "".join("}" if s == "{" else "]" for s in reversed(stack))
    fixed = text + suffix
    try:
        return json_loads(fixed)
    except Exception:
        return None


def _try_extract_facts_regex(text: str):
    """
    通过正则从非标准 JSON 输出中提取 facts。
    处理 LLM 可能返回的各种格式：
      - {"results": [...]}
      - {"data": [...]}
      - 裸 fact 对象列表 [...]
    已知 LLM 会输出嵌套数组如 {"types": ["decision"]}，不能用简单正则
    """
    import re as _re

    # 方法1：尝试找到第一个 [...] 数组块并解析
    # 从 [ 开始，平衡 ] 结束
    def find_json_array(s, start=0):
        """Find a JSON array starting at position start, handling nesting"""
        i = s.find("[", start)
        if i == -1:
            return None, -1
        depth = 0
        in_str = False
        escape = False
        for j in range(i, len(s)):
            c = s[j]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return s[i : j + 1], j + 1
        return None, -1

    # 方法2：尝试找到第一个 {...} 对象块并解析
    def find_json_object(s, start=0):
        """Find a JSON object starting at position start, handling nesting"""
        i = s.find("{", start)
        if i == -1:
            return None, -1
        depth = 0
        in_str = False
        escape = False
        for j in range(i, len(s)):
            c = s[j]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[i : j + 1], j + 1
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
        return None, -1

    facts = []

    # 策略1：找 "facts": [...] 模式
    facts_match = _re.search(r'"facts"\s*:\s*\[', text)
    if facts_match:
        arr_text, end = find_json_array(text, facts_match.start() + len('"facts":'))
        if arr_text:
            try:
                arr = json_loads(arr_text)
                if isinstance(arr, list):
                    for item in arr:
                        if isinstance(item, dict) and item.get("content"):
                            facts.append(item)
            except Exception:
                pass

    # 策略2：直接找数组中的 fact 对象
    if not facts:
        arr_text, end = find_json_array(text)
        if arr_text:
            try:
                arr = json_loads(arr_text)
                if isinstance(arr, list):
                    for item in arr:
                        if isinstance(item, dict) and item.get("content"):
                            facts.append(item)
            except Exception:
                pass

    # 策略3：直接找 {...} fact 对象
    if not facts:
        obj_text, end = find_json_object(text)
        if obj_text:
            try:
                obj = json_loads(obj_text)
                if isinstance(obj, dict) and obj.get("content"):
                    facts.append(obj)
            except Exception:
                pass

    if facts:
        return {"facts": facts}
    return None


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
        parts = text.split("```", 2)
        if len(parts) >= 3:
            text = parts[1]
        else:
            text = parts[0]
        while text.startswith("json"):
            text = text[4:]
        text = text.lstrip("\n\r")
    text = text.strip().strip("`")

    if text and text[0] not in ("{", "["):
        import re as _re

        m = _re.search(r"[\[{]", text)
        if m:
            text = text[m.start() :]

    # 尝试解析，失败时尝试修复截断的 JSON
    data = None
    for _attempt in range(3):
        try:
            data = json_loads(text)
            break
        except Exception:
            if _attempt == 0:
                data = _try_fix_truncated_json(text)
            elif _attempt == 1:
                data = _try_extract_facts_regex(text)
            else:
                break

    if data is None:
        return []

    # LLM 可能返回 {"facts": [...]} 或直接是 [...]
    if isinstance(data, list):
        facts = data
    else:
        facts = data.get("facts", []) if isinstance(data, dict) else []
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
        types = [
            t
            for t in types
            if t
            in (
                "decision",
                "bug-fix",
                "preference",
                "method",
                "todo",
                "unresolved",
                "other",
            )
        ]
        if not types:
            types = ["other"]
        valid.append({**f, "types": types})

    return valid


def extract_dispositions(session_summary: str, l1_facts: list[dict] | None = None) -> list[dict]:
    """
    从会话摘要中提取条件-预测对（dispositions）。
    可选传入已有 L1 facts 作为上下文辅助。
    """
    from .utils import json_loads, llm_generate

    facts_text = ""
    if l1_facts:
        facts_text = "\n".join(
            f"- [{f.get('types', ['?'])[0]}] {f.get('content', '')[:80]}" for f in l1_facts[:10]
        )

    from .config import DISPOSITION_EXTRACT_PROMPT

    prompt = DISPOSITION_EXTRACT_PROMPT % (
        session_summary,
        facts_text or "（无）",
    )

    content = llm_generate(prompt, temperature=1.0, max_tokens=1024)
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 3:
            text = parts[1]
        else:
            text = parts[0]
        while text.startswith("json"):
            text = text[4:]
        text = text.lstrip("\n\r")
    text = text.strip().strip("`")

    import re as _re

    match = _re.search(r"\[[\s\S]*\]", text)
    if match:
        data = json_loads(match.group())
    else:
        data = json_loads(text)

    # 支持 dict-format 或 list-of-lists-format
    if isinstance(data, dict):
        data = data.get("dispositions", [data])

    valid = []
    for item in data:
        if isinstance(item, list) and len(item) >= 6:
            try:
                d = {
                    "condition": str(item[1]),
                    "prediction": str(item[3]),
                    "confidence": float(item[5]) if item[5] is not None else 0.0,
                }
            except (IndexError, ValueError, TypeError):
                continue
        elif isinstance(item, dict):
            d = item
        else:
            continue

        conf = d.get("confidence", 0)
        if conf < 0.6:
            continue
        cond = str(d.get("condition", "")).strip()
        pred = str(d.get("prediction", "")).strip()
        if not cond or not pred:
            continue
        # Guard against empty LLM outputs
        if cond in ("", "null", "None") or pred in ("", "null", "None"):
            continue
        valid.append(d)
    return valid


def store_l1_batch(facts: list[dict], l0_ref: str) -> list[str]:
    """
    将 L1 facts 批量写入数据库（同时生成 embedding）。
    返回写入的 fact_id 列表。
    """
    if not facts:
        return []

    import sqlite3

    texts = [f["content"] for f in facts]
    embeddings = get_embeddings_batch(texts)  # 批量 embedding

    now = datetime.now().isoformat()
    ids = []

    conn = sqlite3.connect(DB_PATH)
    for fact, emb in zip(facts, embeddings, strict=False):
        fid = f"fact_{uuid.uuid4().hex[:8]}"
        ids.append(fid)
        conn.execute(
            """
            INSERT INTO l1_facts
            (id, l0_ref, types, type_confidence, fallback_type,
             content, tags, value, chunk_vector, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """,
            (
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
            ),
        )
    conn.commit()
    conn.close()
    return ids
