#!/usr/bin/env python3
"""
Hermem V4.4 Phase2a - 消费 turn_judgments.jsonl，提取 new_fact_to_l1=true 的事实写入 L1。

设计原则：
- 独立脚本，可在 cron 或手动运行，不阻塞主 agent 流程
- 对每条 new_fact_to_l1=true 的 judgment，提取单轮 user message 中的原子事实
- 写入 L1，source="turn_judgment"
- 去重：最近 5 条 fact hash 缓存在内存，避免同一事实重复写入

用法：
    python3 scripts/process_turn_judgments.py [--dry-run] [--limit N]
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ── Setup ───────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]  # phase3/scripts/ -> phase3/ -> projects/hermem-github
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from impl.config import DB_PATH, L1_EXTRACT_PROMPT
from impl.utils import get_embeddings_batch, llm_generate, serialize_vec, json_dumps


# ── Fact extraction prompt（单轮版本）──────────────────────────────────────────

TURN_FACT_EXTRACT_PROMPT = """你是一个轻量级的事实提取模型。从以下对话片段中提取所有值得写入长期记忆的原子事实。

规则：
- 每个事实只包含一个独立信息点
- 事实必须是具体的、可验证的（人名/项目名/决策/偏好/状态）
- 忽略语气词、礼貌性回复、模糊感慨
- 输出格式严格 JSON 数组，每条事实含 content + types

输出格式：
{{
  "facts": [
    {{"content": "事实内容", "types": ["preference"], "value": "high"}},
    {{"content": "另一个事实", "types": ["decision"], "value": "medium"}}
  ]
}}

对话片段：
{DIALOGUE}

现在提取：
"""


# ── Deduplication cache ───────────────────────────────────────────────────────

class FactCache:
    """内存缓存：最近 N 条 fact 的 hash，用于去重。"""

    def __init__(self, max_size: int = 5):
        self._cache: list[str] = []
        self._max_size = max_size

    def _hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def seen(self, content: str) -> bool:
        h = self._hash(content)
        return h in self._cache

    def add(self, content: str) -> None:
        h = self._hash(content)
        self._cache.append(h)
        if len(self._cache) > self._max_size:
            self._cache.pop(0)


# ── Core logic ───────────────────────────────────────────────────────────────

def extract_turn_facts(dialogue: str) -> list[dict]:
    """从单轮对话片段提取原子事实。"""
    prompt = TURN_FACT_EXTRACT_PROMPT.format(DIALOGUE=dialogue)
    content = llm_generate(prompt, model="qwen3.5:4b-no-think", temperature=0.3, max_tokens=512)

    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 3:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    text = text.strip().strip("`")

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取 JSON 数组
        match = re.search(r'\[[\s\S]+\]', text)
        if match:
            try:
                data = json.loads(match.group())
            except Exception:
                return []
        else:
            return []

    facts = data.get("facts", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    valid = []
    for f in facts:
        c = f.get("content", "").strip()
        if not c:
            continue
        if f.get("value") not in ("high", "medium"):
            f["value"] = "medium"
        if not f.get("types") or not isinstance(f.get("types"), list):
            f["types"] = ["other"]
        valid.append(f)
    return valid


def store_turn_facts(facts: list[dict], l0_ref: str, source: str = "turn_judgment") -> list[str]:
    """批量写入 facts 到 L1，source=turn_judgment。"""
    if not facts:
        return []

    texts = [f["content"] for f in facts]
    embeddings = get_embeddings_batch(texts)

    now = datetime.now().isoformat()
    ids = []

    conn = sqlite3.connect(str(DB_PATH))
    for fact, emb in zip(facts, embeddings):
        fid = f"fact_{uuid.uuid4().hex[:8]}"
        ids.append(fid)
        conn.execute("""
            INSERT INTO l1_facts
            (id, l0_ref, types, type_confidence, fallback_type,
             content, tags, value, chunk_vector, created_at, status, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
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
            source,
        ))
    conn.commit()
    conn.close()
    return ids


def process_judgments(path: Path, dry_run: bool = False, limit: int | None = None) -> dict:
    """
    消费 turn_judgments.jsonl，对 new_fact_to_l1=true 的条目：
    1. 提取单轮事实
    2. 去重（内存 cache）
    3. 写入 L1（dry_run 时跳过）
    返回统计 dict。
    """
    if not path.exists():
        print(f"[Phase2a] 文件不存在: {path}")
        return {"skipped": 0, "extracted": 0, "stored": 0, "duplicates": 0}

    cache = FactCache(max_size=500)
    stats = {"total": 0, "new_fact_true": 0, "extracted": 0, "stored": 0, "duplicates": 0, "errors": 0}

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if limit:
        lines = lines[-limit:]

    for line in lines:
        stats["total"] += 1
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            stats["errors"] += 1
            continue

        judgment = entry.get("judgment", {})
        if not judgment.get("new_fact_to_l1", False):
            continue

        stats["new_fact_true"] += 1

        # 构建对话片段（user_content_preview 是截断的，用 judgment 的 keywords 帮助理解）
        user_preview = entry.get("user_content_preview", "")
        session_id = entry.get("session_id", "unknown")
        dialogue = user_preview  # 用截断的 preview

        # 提取事实
        facts = extract_turn_facts(dialogue)
        if not facts:
            continue
        stats["extracted"] += len(facts)

        # 去重 + 写入
        to_store = []
        for fact in facts:
            if cache.seen(fact["content"]):
                stats["duplicates"] += 1
                continue
            cache.add(fact["content"])
            to_store.append(fact)

        if to_store:
            l0_ref = f"turn_judgment_{session_id}"
            if dry_run:
                print(f"[DRY-RUN] session={session_id} turn={entry.get('turn_counter')} would store {len(to_store)} facts")
                for fact in to_store:
                    print(f"  -> {fact['content'][:80]}")
            else:
                ids = store_turn_facts(to_store, l0_ref, source="turn_judgment")
                stats["stored"] += len(ids)

    return stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Hermem V4.4 Phase2a: process turn_judgments.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="不写入，只打印")
    parser.add_argument("--limit", type=int, default=None, help="只处理最近 N 条")
    args = parser.parse_args()

    journal_path = Path.home() / ".hermes" / "memory" / "turn_judgments.jsonl"

    print(f"[Phase2a] 输入: {journal_path}")
    print(f"[Phase2a] 模式: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    if args.limit:
        print(f"[Phase2a] limit: {args.limit}")

    stats = process_judgments(journal_path, dry_run=args.dry_run, limit=args.limit)

    print(f"\n[Phase2a] 完成:")
    print(f"  总 judgment 条目: {stats['total']}")
    print(f"  new_fact_to_l1=true: {stats['new_fact_true']}")
    print(f"  提取到 facts 数: {stats['extracted']}")
    print(f"  写入 L1 数: {stats['stored']}")
    print(f"  跳过（去重）: {stats['duplicates']}")
    print(f"  错误: {stats['errors']}")


if __name__ == "__main__":
    main()
