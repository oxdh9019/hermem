#!/usr/bin/env python3
"""
V4.2 迁移脚本：从 L0 JSON 文件批量提取 dispositions。
遍历所有会话，调用 extract_dispositions() 生成条件-预测对。
幂等设计：已有 disposition 的会话不重复处理。
"""
import sys, json, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "phase3"))

from impl.config import DB_PATH
from impl.l1_extract import extract_dispositions
from impl.utils import get_embedding, serialize_vec
import sqlite3
from datetime import datetime

BATCH_SIZE = 3  # 每次最多处理会话数（LLM 调用慢）


def load_conversation_from_db(session_id: str) -> str | None:
    """从 state.db 读取会话消息，构建对话文本。"""
    try:
        import sqlite3
        conn = sqlite3.connect(Path.home() / ".hermes" / "state.db")
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY timestamp",
            (session_id,)
        ).fetchall()
        conn.close()
        if not rows:
            return None
        return "\n".join(
            f"[{r[0]}] {str(r[1])[:300]}"
            for r in rows if r[1]
        )
    except Exception:
        return None


def load_conversation_from_l0(session_id: str) -> str | None:
    """从 L0 JSON 读取消息。"""
    try:
        l0_path = Path.home() / ".hermes" / "memory" / "l0_raw" / f"{session_id}.json"
        if not l0_path.exists():
            return None
        data = json.loads(l0_path.read_text(encoding="utf-8"))
        msgs = data.get("messages", [])
        return "\n".join(
            f"[{m.get('role','?')}] {str(m.get('content',''))[:300]}"
            for m in msgs if m.get("content")
        )
    except Exception:
        return None


def build_summary(conversation: str) -> str:
    """从对话生成 session_summary。"""
    from impl.utils import llm_generate
    prompt = f"""简要总结以下对话的核心内容和结论（3-5句中文）：

{conversation[:3000]}
"""
    return llm_generate(prompt, temperature=0.3, max_tokens=300)


def disposition_exists(conn: sqlite3.Connection, session_id: str) -> bool:
    """检查该会话是否已有 disposition。"""
    row = conn.execute(
        "SELECT 1 FROM l1_dispositions WHERE l0_ref = ? LIMIT 1",
        (session_id,)
    ).fetchone()
    return row is not None


def save_dispositions(session_id: str, dispositions: list[dict], conn: sqlite3.Connection) -> int:
    """将 disposition 列表写入 DB。返回写入条数。"""
    saved = 0
    for d in dispositions:
        cond_emb = get_embedding(d["condition"])
        pred_emb = get_embedding(d["prediction"])
        now = datetime.now().isoformat()
        disp_id = f"disp_{datetime.now().strftime('%Y%m%H%M%S')}_{hash(session_id) % 100000:05d}_{saved}"
        conn.execute("""
            INSERT INTO l1_dispositions
            (id, l0_ref, condition_text, prediction_text,
             condition_embedding, prediction_embedding,
             confidence, source_session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            disp_id,
            session_id,
            d["condition"],
            d["prediction"],
            serialize_vec(cond_emb.tolist()),
            serialize_vec(pred_emb.tolist()),
            d.get("confidence", 1.0),
            session_id,
            now,
        ))
        saved += 1
    return saved


def run():
    import sqlite3 as _sqlite3

    # 从 state.db 获取所有会话，按时间排序（新的在前）
    state_conn = _sqlite3.connect(str(Path.home() / ".hermes" / "state.db"))
    sessions = state_conn.execute("""
        SELECT s.id, COUNT(m.id) as msg_count,
               SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) as user_msgs
        FROM sessions s
        JOIN messages m ON m.session_id = s.id
        GROUP BY s.id
        HAVING user_msgs >= 3
        ORDER BY s.started_at DESC
    """).fetchall()
    state_conn.close()

    l0_dir = Path.home() / ".hermes" / "memory" / "l0_raw"
    db_conn = _sqlite3.connect(DB_PATH)

    print(f"发现 {len(sessions)} 个有实质对话的会话（>=3条用户消息）。")

    # 过滤出还未处理的
    pending = [s for s in sessions if not disposition_exists(db_conn, s[0])]
    print(f"其中 {len(pending)} 个会话尚未提取 dispositions。")
    print(f"本次处理最多 {BATCH_SIZE} 个。\n")

    if not pending:
        total = db_conn.execute("SELECT COUNT(*) FROM l1_dispositions").fetchone()[0]
        print(f"全部 {total} 个会话已提取完毕。")
        db_conn.close()
        return

    pending = pending[:BATCH_SIZE]

    total_saved = 0
    total_skipped = 0
    total_no_conv = 0

    for session_id, msg_count, user_msgs in pending:
        print(f"  [{session_id}] msgs={msg_count}, user={user_msgs}", end=" ", flush=True)

        # 优先从 L0 JSON 读取，fallback 到 state.db
        conversation = load_conversation_from_l0(session_id)
        if not conversation:
            conversation = load_conversation_from_db(session_id)

        if not conversation:
            print("无对话记录，跳过")
            total_no_conv += 1
            continue

        try:
            summary = build_summary(conversation)
        except Exception as e:
            print(f"摘要生成失败: {e}")
            continue

        try:
            dispositions = extract_dispositions(summary, None)
        except Exception as e:
            print(f"disposition 提取失败: {e}")
            continue

        if not dispositions:
            print("无 disposition")
            total_skipped += 1
            continue

        saved = save_dispositions(session_id, dispositions, db_conn)
        total_saved += saved
        print(f"✓ {saved} 条")

    db_conn.commit()

    total_disp = db_conn.execute("SELECT COUNT(*) FROM l1_dispositions").fetchone()[0]
    remaining = sum(1 for s in sessions if not disposition_exists(db_conn, s[0]))

    print(f"\n完成：新增 {total_saved} 条 dispositions，"
          f"{total_skipped} 个会话无 disposition，"
          f"{total_no_conv} 个无对话")
    print(f"dispositions 表共 {total_disp} 条，剩余 {remaining} 个会话待处理。")
    print("重复运行以继续迁移。")
    db_conn.close()


if __name__ == "__main__":
    run()
