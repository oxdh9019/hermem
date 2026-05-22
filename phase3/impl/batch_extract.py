#!/usr/bin/env python3
"""
从 state.db 批量提取会话摘要，再跑 L1 提取，收集足量 facts 做模拟测试。
"""

import os
import random
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT + "/phase3")  # 显式指向 phase3/impl/ 而非 legacy impl/
os.chdir(PROJECT_ROOT + "/phase3")

from datetime import datetime

from impl import extract_l1_facts, save_l0_raw, store_l1_batch, try_aggregate_l2

DB = os.environ.get("HERMES_STATE_DB", str(Path.home() / ".hermes" / "state.db"))
OUT_DB = os.environ.get("HERMEM_DB", str(Path.home() / ".hermes" / "memory" / "l0_l3.db"))


def build_session_summary(conn, session_id: str) -> str:
    """从 messages 表聚合单个会话的文本内容"""
    rows = conn.execute(
        """
        SELECT role, content FROM messages
        WHERE session_id = ?
        ORDER BY timestamp ASC
    """,
        [session_id],
    ).fetchall()

    parts = []
    for role, content in rows:
        if not content:
            continue
        # 截断过长消息
        if len(content) > 2000:
            content = content[:2000] + "...[截断]"
        prefix = "User" if role == "user" else "Assistant"
        parts.append(f"{prefix}: {content}")

    return "\n".join(parts)


def main():
    conn = sqlite3.connect(DB)

    # 取最近的 50 个非 cron 会话
    sessions = conn.execute("""
        SELECT id, started_at, ended_at, title
        FROM sessions
        WHERE source != 'cron'
        ORDER BY started_at DESC
        LIMIT 50
    """).fetchall()

    conn.close()
    print(f"处理 {len(sessions)} 个会话...")

    all_facts = []
    failed = 0

    for i, (sid, started, ended, title) in enumerate(sessions):
        started_dt = (
            datetime.fromtimestamp(started).isoformat() if started else datetime.now().isoformat()
        )
        ended_dt = (
            datetime.fromtimestamp(ended).isoformat() if ended else datetime.now().isoformat()
        )

        # 跳过 title 过长的
        if title and len(str(title)) > 500:
            title = str(title)[:500]

        try:
            # 读取消息构建摘要
            msg_conn = sqlite3.connect(DB)
            content = build_session_summary(msg_conn, sid)
            msg_conn.close()

            if not content.strip():
                continue

            # L0 存档
            l0_ref = save_l0_raw(
                sid,
                [{"role": "auto", "content": content[:5000]}],
                started_dt,
                ended_dt,
            )

            # L1 提取（截断到 3000 字符防止超时）
            summary_for_extract = content[:3000]
            facts = extract_l1_facts(summary_for_extract)

            if facts:
                # 写入 OUT_DB
                fact_ids = store_l1_batch(facts, l0_ref)
                written = [{**f, "id": fid} for f, fid in zip(facts, fact_ids, strict=False)]
                try_aggregate_l2(written)
                all_facts.extend([(f, sid) for f in facts])
                print(f"  [{i + 1:2d}] {sid[:20]}... → {len(facts)} facts")
            else:
                print(f"  [{i + 1:2d}] {sid[:20]}... → 0 facts")

        except Exception as e:
            failed += 1
            print(f"  [{i + 1:2d}] ERROR: {e}")

    print(f"\n完成: {len(all_facts)} facts from {len(sessions)} sessions")
    print(f"失败: {failed}")

    # 输出统计
    from collections import Counter

    type_counter = Counter()
    value_counter = Counter()
    for f, _ in all_facts:
        for t in f.get("types", []):
            type_counter[t] += 1
        value_counter[f.get("value", "?")] += 1

    print("\n按类型分布:")
    for t, cnt in type_counter.most_common():
        print(f"  {t}: {cnt}")
    print("\n按价值分布:")
    for v, cnt in value_counter.most_common():
        print(f"  {v}: {cnt}")

    # 随机选 20 条给你抽检
    if all_facts:
        random.seed(42)
        sample = random.sample(all_facts, min(20, len(all_facts)))
        print("\n=== 随机样本（供 用户 抽检）===")
        for j, (f, sid) in enumerate(sample, 1):
            print(f"\n{j:2d}. types={f['types']} value={f['value']}")
            print(f"   content: {f['content'][:100]}")
            print(f"   tags: {f.get('tags', [])}")
            print(f"   session: {sid[:20]}")


if __name__ == "__main__":
    main()
