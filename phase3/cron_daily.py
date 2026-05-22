#!/usr/bin/env python3
"""
Hermem Phase 3 - 每日定时处理脚本
从 state.db 拉取新会话 → L1 提取 → L2 聚合 → L3 staging

触发时间: 每天 6:00 和 18:00
"""
import sys, os, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "phase3"))
os.chdir(str(PROJECT_ROOT / "phase3"))

from impl import (
    extract_l1_facts, store_l1_batch,
    try_aggregate_l2, batch_stage_from_l1,
    save_l0_raw, load_l0_detail,
)
from impl.async_annotation import start_worker, stop_worker, enqueue_annotation
from datetime import datetime
import sqlite3, json

STATE_DB = Path.home() / ".hermes" / "state.db"
OUT_DB   = Path.home() / ".hermes" / "memory" / "l0_l3.db"
L0_DIR   = Path.home() / ".hermes" / "memory" / "l0_raw"


def build_session_text(conn, session_id: str) -> str:
    rows = conn.execute("""
        SELECT role, content FROM messages
        WHERE session_id = ?
        ORDER BY timestamp ASC
    """, [session_id]).fetchall()
    parts = []
    for role, content in rows:
        if not content:
            continue
        if len(content) > 2000:
            content = content[:2000] + "[截断]"
        prefix = "User" if role == "user" else "Assistant"
        parts.append(f"{prefix}: {content}")
    return "\n".join(parts)


def main():
    print(f"[Hermem Phase3 cron] 开始 {datetime.now().isoformat()}")

    # 找出已有 L0 的 session，避免重复处理
    existing_l0 = set()
    if L0_DIR.exists():
        for f in L0_DIR.glob("*.json"):
            # 文件名即 session_id（无 "l0_" 前缀，l0_ref="l0_{session_id}" 只是引用格式）
            existing_l0.add(f.stem)

    conn = sqlite3.connect(STATE_DB)
    # 只处理非 cron、会话时长 > 60s 的会话，按时间倒序
    sessions = conn.execute("""
        SELECT id, started_at, ended_at, title
        FROM sessions
        WHERE source != 'cron'
          AND ended_at IS NOT NULL
          AND (ended_at - started_at) > 60
        ORDER BY started_at DESC
        LIMIT 30
    """).fetchall()
    conn.close()

    print(f"  发现 {len(sessions)} 个最近会话，已处理 {len(existing_l0)} 个 L0")

    processed = 0
    skipped = 0
    failed = 0

    # 启动 annotation worker（后台线程）
    start_worker()

    for sid, started, ended, title in sessions:
        if sid in existing_l0:
            skipped += 1
            continue

        started_dt = datetime.fromtimestamp(started).isoformat() if started else datetime.now().isoformat()
        ended_dt = datetime.fromtimestamp(ended).isoformat() if ended else datetime.now().isoformat()
        if title and len(str(title)) > 500:
            title = str(title)[:500]

        try:
            # 读取消息文本
            msg_conn = sqlite3.connect(STATE_DB)
            content = build_session_text(msg_conn, sid)
            msg_conn.close()

            if not content.strip():
                skipped += 1
                continue

            # L0 存档
            l0_ref = save_l0_raw(
                sid,
                [{"role": "auto", "content": content[:10000]}],
                started_dt,
                ended_dt,
            )

            # L1 提取
            summary = content[:3000]
            facts = extract_l1_facts(summary)

            if facts:
                # 写入 L1
                fact_ids = store_l1_batch(facts, l0_ref)
                written = [{**f, "id": fid} for f, fid in zip(facts, fact_ids)]
                # L2 聚合
                try_aggregate_l2(written)
                # L3 staging
                batch_stage_from_l1(written, source=sid)
                # Annotation 入队（异步，不阻塞）
                enqueue_annotation(sid, summary, facts)

                print(f"  [✓] {sid[:20]} → {len(facts)} facts")
                processed += 1
            else:
                print(f"  [-] {sid[:20]} → 0 facts")

        except Exception as e:
            print(f"  [✗] {sid[:20]}: {e}")
            failed += 1

    print(f"\n[Hermem Phase3 cron] 完成: {processed} 新会话处理, {skipped} 已跳过, {failed} 失败")
    print(f"  L1 总量: {sqlite3.connect(OUT_DB).execute('SELECT COUNT(*) FROM l1_facts').fetchone()[0]}")
    print(f"  L2 总量: {sqlite3.connect(OUT_DB).execute('SELECT COUNT(*) FROM l2_scenes').fetchone()[0]}")
    print(f"  L3 staging: {sqlite3.connect(OUT_DB).execute('SELECT COUNT(*) FROM l3_staging').fetchone()[0]}")

    # 等待 annotation 队列处理完毕（最多 120 秒）
    print(f"\n[Annotation] 等待队列清空...")
    stop_worker(wait=True)
    print(f"[Annotation] 队列已清空，cron 结束")


if __name__ == "__main__":
    main()