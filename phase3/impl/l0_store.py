#!/usr/bin/env python3
"""
Hermem Phase 3 - L0 原始会话存档
Step 1a: save_l0_raw() + enforce_l0_quota()
"""
import json
from pathlib import Path
from datetime import datetime

L0_DIR = Path.home() / ".hermes" / "memory" / "l0_raw"
QUOTA_BYTES = 500 * 1024 * 1024  # 500MB，可通过 HERMEM_L0_QUOTA 环境变量覆盖


def _quota() -> int:
    import os
    return int(os.environ.get("HERMEM_L0_QUOTA", QUOTA_BYTES))


def save_l0_raw(
    session_id: str,
    messages: list,
    start: str,
    end: str,
) -> str:
    """
    保存原始会话到 L0 JSON 文件。

    参数:
        session_id:  会话 ID（不含 l0_ 前缀）
        messages:    messages 数组（role/content/ts/tool_calls）
        start:       ISO 8601 开始时间
        end:         ISO 8601 结束时间

    返回:
        l0_ref，格式为 "l0_{session_id}"
    """
    l0_ref = f"l0_{session_id}"
    L0_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "session_id": session_id,
        "l0_ref":    l0_ref,
        "start":     start,
        "end":       end,
        "compressed": False,
        "messages":  messages,
    }

    # 大会话压缩过大的 tool_calls
    _maybe_compress(payload)

    path = L0_DIR / f"{session_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    enforce_l0_quota()
    return l0_ref


def _maybe_compress(payload: dict):
    """对过长 messages 中的 tool_calls 做简化标记"""
    for m in payload["messages"]:
        tc = m.get("tool_calls")
        if tc is None:
            continue
        s = json.dumps(tc, ensure_ascii=False)
        if len(s) > 5000:
            m["tool_calls"] = "[compressed]"
            payload["compressed"] = True


def enforce_l0_quota():
    """超出配额时，删除最旧的会话直到低于 80% 配额"""
    quota = _quota()
    if not L0_DIR.exists():
        return

    files = sorted(L0_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    if total <= quota:
        return

    target = int(quota * 0.8)
    for f in files:
        if total < target:
            break
        size = f.stat().st_size
        total -= size
        f.unlink()
        print(f"  [l0 gc] deleted {f.name} ({size // 1024}KB)")


def load_l0_detail(l0_ref: str, context_hint: str = None) -> str:
    """
    按需读取 L0 原始会话。

    参数:
        l0_ref:       l0_xxx 格式的引用
        context_hint: 可选关键词，只返回包含该词的 messages

    返回:
        JSON 字符串，失败时返回错误描述
    """
    session_id = l0_ref.replace("l0_", "")
    l0_path = L0_DIR / f"{session_id}.json"
    if not l0_path.exists():
        return json.dumps({"error": "L0 not found or expired", "l0_ref": l0_ref})

    with open(l0_path) as f:
        l0 = json.load(f)

    if not context_hint:
        return json.dumps(l0, ensure_ascii=False)

    hint = context_hint.lower()
    relevant = [
        m for m in l0["messages"]
        if hint in m.get("content", "").lower()
        or (isinstance(m.get("tool_calls"), str) and hint in m["tool_calls"].lower())
    ]
    return json.dumps({**l0, "messages": relevant}, ensure_ascii=False, indent=2)


# ── CLI 快速测试 ────────────────────────────────────────────
if __name__ == "__main__":
    import sys, uuid

    test_msg = [
        {"role": "user", "content": "测试会话", "ts": datetime.now().isoformat()},
        {"role": "assistant", "content": "测试回复", "ts": datetime.now().isoformat()},
    ]
    sid = f"test_{uuid.uuid4().hex[:8]}"
    ref = save_l0_raw(sid, test_msg, datetime.now().isoformat(), datetime.now().isoformat())
    print(f"saved l0_ref={ref}")

    loaded = load_l0_detail(ref)
    print(f"loaded: {loaded[:100]}...")

    # 验证
    from pathlib import Path
    assert (L0_DIR / f"{sid}.json").exists(), "L0 file not created"
    print("✓ Step 1a 基础测试通过")


# ── Error Annotation ─────────────────────────────────────────────────────────

def annotate_l0_after_l1(
    session_id: str,
    session_summary: str,
    l1_facts: list[dict],
    annotation_model: str = "MiniMax-M2.7",
) -> dict | None:
    """
    对已存在的 L0 文件补充 error_annotation。

    在 process_session() 的 L1 提取之后调用。
    基于 L1 facts 反查 L0，对比识别助手的预测误差。

    逻辑：
    1. 读取当前 L0 JSON
    2. 幂等检查：已有 error_annotation 则直接返回
    3. 调用 LLM 生成 annotation
    4. 写回 L0 JSON（保持其他字段不变）

    返回：
        annotation dict 成功，None 失败
    """
    import re
    from datetime import datetime
    from .utils import llm_generate  # type: ignore # noqa: E405

    l0_path = L0_DIR / f"{session_id}.json"
    if not l0_path.exists():
        print(f"  [error_annotation] L0 不存在，跳过: {session_id}")
        return None

    with open(l0_path) as f:
        l0 = json.load(f)

    # 幂等：已有 annotation 则跳过
    if "error_annotation" in l0:
        return l0["error_annotation"]

    # 构造 L1 facts 摘要文本（用于 prompt）
    if l1_facts:
        facts_text = "\n".join(
            f"- [{f.get('types', ['?'])[0]}] {f.get('content', '')[:80]}"
            for f in l1_facts[:10]  # 最多10条，避免过长
        )
    else:
        facts_text = "（无提取到原子事实）"

    # 调用 LLM
    from .config import ERROR_ANNOTATION_PROMPT

    prompt = ERROR_ANNOTATION_PROMPT.format(
        SESSION_SUMMARY=session_summary,
        L1_FACTS=facts_text,
    )
    content = llm_generate(
        prompt,
        model=annotation_model,
        temperature=0.2,
        max_tokens=1024,
    )

    # 解析 JSON（可能被 markdown 包裹）
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 3:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
        else:
            text = parts[1] if len(parts) > 1 else text
    text = text.strip().strip("`")

    # 提取第一个完整 JSON 对象
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        print(f"  [error_annotation] LLM返回非JSON，跳过: {text[:100]}")
        return None

    try:
        annotation = json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"  [error_annotation] JSON解析失败，跳过: {e}")
        return None

    # 补充 meta 字段
    annotation["annotated_at"] = datetime.now().isoformat()
    annotation["model"] = annotation_model

    # 写回 L0
    l0["error_annotation"] = annotation
    l0_path.write_text(json.dumps(l0, ensure_ascii=False, indent=2))

    print(
        f"  [error_annotation] 写入成功 surprise={annotation.get('surprise_level')} "
        f"errors={len(annotation.get('prediction_errors', []))}"
    )
    return annotation


# ── Error Annotation V2 ───────────────────────────────────────────────────────

import re as _re


def _mark_corrections_in_transcript(transcript: str) -> str:
    """
    可选预处理：标记用户可能纠正或否定的句子。
    在用户发言中插入 [可能纠正] 标签，帮助 LLM 聚焦。
    """
    correction_patterns = [
        r'不对', r'实际上', r'应该是', r'纠正', r'我理解是',
        r'注意顺序', r'不是', r'错了', r'反过来',
    ]
    pattern = '|'.join(correction_patterns)
    lines = transcript.split('\n')
    marked = []
    for line in lines:
        if line.startswith('用户:') and _re.search(pattern, line):
            line = line.replace('用户:', '[可能纠正] 用户:', 1)
        marked.append(line)
    return '\n'.join(marked)


def annotate_l0_after_l1_v2(
    session_id: str,
    session_summary: str,
    l1_facts: list[dict],
    annotation_model: str = "MiniMax-M2.7",
    use_preprocessing: bool = True,
    force: bool = False,
) -> dict | None:
    """
    V2 版本：增强型预测误差标注。

    新增特性：
    - 更严格的 prompt（few-shot + 事实约束）
    - 可选的对话预处理（标记用户纠正点）
    - 重试机制

    参数：
        session_id:        会话 ID
        session_summary:    对话摘要
        l1_facts:          已提取的 L1 facts
        annotation_model:  LLM 模型名称
        use_preprocessing: 是否启用对话预处理
        force:             强制重新生成，忽略已有 annotation
    """
    from datetime import datetime as _dt
    from .utils import llm_generate  # type: ignore
    from .config import ERROR_ANNOTATION_PROMPT

    l0_path = L0_DIR / f"{session_id}.json"
    if not l0_path.exists():
        print(f"  [annotate_v2] L0 不存在: {session_id}")
        return None

    with open(l0_path) as f:
        l0 = json.load(f)

    # 幂等检查
    if not force and "error_annotation" in l0:
        return l0["error_annotation"]

    # 构造对话文本（从 session_summary + l1_facts）
    transcript_parts = [f"【对话摘要】\n{session_summary}"]
    if l1_facts:
        facts_text = "\n".join(
            f"- [{f.get('types', ['?'])[0]}] {f.get('content', '')[:80]}"
            for f in l1_facts[:10]
        )
        transcript_parts.append(f"\n【助手提取的原子事实】\n{facts_text}")
    transcript = "\n".join(transcript_parts)

    # 预处理
    if use_preprocessing:
        transcript = _mark_corrections_in_transcript(transcript)

    # 调用 LLM（带重试）
    prompt = ERROR_ANNOTATION_PROMPT.format(
        SESSION_SUMMARY=session_summary,
        L1_FACTS=facts_text if l1_facts else "（无）",
    )

    max_retries = 2
    annotation = None

    for attempt in range(max_retries):
        try:
            content = llm_generate(
                prompt,
                model=annotation_model,
                temperature=0.2,
                max_tokens=1024,
            )

            text = content.strip()
            if text.startswith("```"):
                parts = text.split("```", 2)
                if len(parts) >= 3:
                    text = parts[1]
                    if text.startswith("json"):
                        text = text[4:]
                else:
                    text = parts[1] if len(parts) > 1 else text
            text = text.strip().strip("`")

            match = _re.search(r'\{[\s\S]*\}', text)
            if not match:
                raise ValueError("No JSON object found")
            annotation = json.loads(match.group())

            if "prediction_errors" not in annotation:
                raise ValueError("Missing prediction_errors field")
            break
        except Exception as e:
            print(f"  [annotate_v2] 尝试 {attempt + 1}/{max_retries} 失败: {e}")
            if attempt == max_retries - 1:
                return None
            import time as _time
            _time.sleep(1)

    # 补充 meta
    annotation["annotated_at"] = _dt.now().isoformat()
    annotation["model"] = annotation_model
    annotation["version"] = "v2"

    # 写回
    l0["error_annotation"] = annotation
    l0_path.write_text(json.dumps(l0, ensure_ascii=False, indent=2))

    print(
        f"  [annotate_v2] 写入成功 surprise={annotation.get('surprise_level')} "
        f"errors={len(annotation.get('prediction_errors', []))}"
    )
    return annotation
