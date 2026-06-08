#!/usr/bin/env python3
"""Hermem V6 Sprint 0.5 - 数据库迁移脚本。

在 hermem.db 中新建 recall_outcome 表,记录 V5 active retrieval 注入后的
用户行为闭环数据(follow_up_type: used / ignored / rejected)。

可重入:已存在的表/索引会跳过(IF NOT EXISTS)。
用法: python3 phase3/impl/migrate_v6_sprint05.py
"""

import sqlite3
from pathlib import Path

HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"

MIGRATIONS = [
    # recall_outcome 表(Sprint 0.5 行为闭环核心)
    """
    CREATE TABLE IF NOT EXISTS recall_outcome (
        recall_id              TEXT PRIMARY KEY,
        session_id             TEXT NOT NULL,
        chunk_id               TEXT NOT NULL,
        similarity             REAL,
        tier                   TEXT,  -- 'high' / 'medium'
        anchor_source          TEXT,  -- 'frequency' / 'anchor_keyword' / 'temporal' / 'disposition_error' / 'predictive' / NULL
        follow_up_type         TEXT,  -- 'used' / 'ignored' / 'rejected' / NULL(待 3 轮内异步检测)
        follow_up_turn_count   INTEGER,  -- 这次 recall 后用户又聊了几轮
        created_at             REAL DEFAULT (julianday('now')),
        follow_up_resolved_at  REAL  -- NULL = 未解析;非 NULL = 异步检测完成时间
    );
    """,
    # session + chunk 索引(支撑按 session / chunk 查询 recall 历史)
    "CREATE INDEX IF NOT EXISTS idx_recall_outcome_session ON recall_outcome(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_recall_outcome_chunk ON recall_outcome(chunk_id);",
    # 部分索引:未解析的 recall(后台 worker 扫描)
    "CREATE INDEX IF NOT EXISTS idx_recall_outcome_unresolved ON recall_outcome(follow_up_resolved_at) WHERE follow_up_resolved_at IS NULL;",
    # follow_up_type 索引(评测 hit rate 提升)
    "CREATE INDEX IF NOT EXISTS idx_recall_outcome_type ON recall_outcome(follow_up_type);",
]


def _safe_execute(cur, sql, name):
    try:
        cur.execute(sql)
        print(f"  ok  {name}")
        return True
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "duplicate column" in msg or "already exists" in msg or "duplicate index" in msg:
            print(f"  -- {name} (already exists, skipped)")
            return True
        print(f"  ERR {name}: {e}")
        return False


def migrate():
    print("=" * 50)
    print("Hermem V6 Sprint 0.5 - recall_outcome migration")
    print("=" * 50)

    if not HERMEM_DB.exists():
        print(f"  ! {HERMEM_DB} does not exist - nothing to migrate")
        return

    conn = sqlite3.connect(str(HERMEM_DB))
    cur = conn.cursor()
    print(f"Connecting: {HERMEM_DB}")

    for sql in MIGRATIONS:
        sql_upper = sql.upper().strip()
        if "CREATE TABLE" in sql_upper:
            name = (
                "table:" + sql.split("CREATE TABLE IF NOT EXISTS ")[-1].split(" ")[0].split("(")[0]
            )
        elif "CREATE INDEX" in sql_upper:
            name = "idx:" + sql.split("CREATE INDEX IF NOT EXISTS ")[-1].split(" ")[0]
        else:
            name = "op"
        if not _safe_execute(cur, sql, name):
            conn.close()
            raise RuntimeError(f"migration failed: {sql[:80]}")

    conn.commit()
    conn.close()
    print("=" * 50)
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
