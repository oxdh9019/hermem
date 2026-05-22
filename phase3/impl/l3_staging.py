#!/usr/bin/env python3
"""
Hermem Phase 3 - L3 人格提炼
Step 5a: stage_preference() — 将 preference 推入 staging area
Step 5b: process_l3_staging() + confirm_preference()
"""

import uuid
from datetime import datetime

from .config import DB_PATH, PROFILE_PATH, STAGING_CONFIRM_THRESHOLD


def stage_preference(fact_id: str, content: str, source: str):
    """
    当 L1 中有 type=preference 时，存入 staging area。
    """
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    sid = f"staging_{uuid.uuid4().hex[:8]}"
    conn.execute(
        """
        INSERT OR IGNORE INTO l3_staging
        (id, fact_id, content, source, created_at, confirmed)
        VALUES (?, ?, ?, ?, ?, 0)
    """,
        (sid, fact_id, content, source, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return sid


def get_pending_preferences(limit: int = None) -> list[dict]:
    """返回待确认的 preference 列表"""
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    lim = limit or STAGING_CONFIRM_THRESHOLD
    rows = conn.execute(
        "SELECT id, fact_id, content, source, created_at "
        "FROM l3_staging WHERE confirmed=0 ORDER BY created_at LIMIT ?",
        [lim],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def process_l3_staging(notify_fn=None):
    """
    每日定时任务：
    - staging 满 5 条时，推送确认消息给 用户
    - notify_fn(msg_str) 为发送函数（如飞书/微信发送）

    用户 回复后，由调用方根据回复调用 confirm_preference() 或 reject_preference()
    """
    pending = get_pending_preferences()
    if len(pending) < STAGING_CONFIRM_THRESHOLD:
        return {"status": "waiting", "pending_count": len(pending)}

    msg = "以下是从最近会话中提取的偏好，请确认哪些想保留到个人画像：\n\n"
    for i, p in enumerate(pending[:STAGING_CONFIRM_THRESHOLD], 1):
        msg += f"{i}. {p['content']}\n   (来源: {p['source']})\n"
    msg += "\n回复编号确认（如 1,3），回复「跳过」忽略本次"

    if notify_fn:
        notify_fn(msg)

    return {
        "status": "notified",
        "message": msg,
        "items": pending[:STAGING_CONFIRM_THRESHOLD],
    }


def confirm_preference(staging_id: str) -> bool:
    """
    用户 确认后，将 staging 条目标记为 confirmed，
    并追加到 user_profile.md。
    """
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT content, source FROM l3_staging WHERE id=? AND confirmed=0",
        [staging_id],
    ).fetchone()
    if not row:
        conn.close()
        return False

    conn.execute("UPDATE l3_staging SET confirmed=1 WHERE id=?", [staging_id])
    conn.commit()
    conn.close()

    # 追加到 user_profile.md
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PROFILE_PATH.exists():
        PROFILE_PATH.write_text("# 用户 个人画像\n\n## 核心偏好（Confirmed）\n")

    entry = f"- {row[0]} (来源: {row[1]})\n"
    with open(PROFILE_PATH, "a") as f:
        f.write(entry)

    print(f"  [L3] confirmed: {row[0][:50]}")
    return True


def reject_preference(staging_id: str) -> bool:
    """用户 拒绝，标记为 rejected"""
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE l3_staging SET confirmed=-1 WHERE id=?", [staging_id])
    conn.commit()
    conn.close()
    return True


def batch_stage_from_l1(l1_facts: list[dict], source: str):
    """
    从 L1 facts 中提取所有 type=preference，批量推入 staging。
    每次 L1 提取后调用此函数。
    """
    for fact in l1_facts:
        types = fact.get("types", [])
        if "preference" in types:
            stage_preference(
                fact_id=fact["id"],
                content=fact["content"],
                source=source,
            )
