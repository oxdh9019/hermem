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
        total -= f.stat().st_size
        f.unlink()
        print(f"  [l0 gc] deleted {f.name} ({f.stat().st_size // 1024}KB)")


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
