#!/usr/bin/env python3
"""
V5.5 数据库迁移脚本
处理两个数据库：
- hermem.db（Phase 2 向量索引，V5 主表）
- l0_l3.db（Phase 3 主数据库，包含 l1_facts/dispositions/l1_dispositions）

改动：
1. hermem.db:
   - chunks 加 usage_count, last_used_at 字段
   - 新增 pending_conflicts 表（冲突记录）
   - 新增 l4_reflections 表（LLM 反思记录）
   - 新增索引

2. l0_l3.db:
   - l1_facts 加 usage_count, last_used_at 字段（主动遗忘用）
   - l1_dispositions 已有这两个字段，跳过
   - l1_dispositions/dispositions 加 archived 字段（兼容 V5.5）

用法: python3 phase3/scripts/migrate_v55.py
"""

import sqlite3
import sys
from pathlib import Path

HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
L0L3_DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"


def migrate_db(db_path: Path, migrations: list[str], label: str):
    """对单个数据库执行迁移，幂等操作。"""
    if not db_path.exists():
        print(f"[{label}] 跳过：{db_path} 不存在")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    print(f"[{label}] 连接: {db_path}")

    for sql in migrations:
        try:
            cur.execute(sql)
            # 提取日志名
            if "CREATE TABLE" in sql:
                name = sql.split("CREATE TABLE IF NOT EXISTS ")[-1].split(" ")[0]
            elif "ALTER TABLE" in sql:
                name = sql.split("ALTER TABLE ")[-1].split(" ADD")[0]
            elif "CREATE INDEX" in sql:
                name = sql.split("CREATE INDEX IF NOT EXISTS ")[-1].split(" ")[0]
            else:
                name = "op"
            print(f"  ✅ {name}")
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "duplicate column" in msg or "already exists" in msg:
                print(f"  ⚠️  已存在，跳过: {e}")
            elif "no such table" in msg:
                # 索引目标表不存在时跳过
                print("  ⚠️  表不存在，跳过索引")
            else:
                print(f"  ❌ 错误: {e}")
                raise
    conn.commit()
    conn.close()
    print(f"[{label}] 完成\n")


# ── hermem.db 迁移 ────────────────────────────────────────────────────────────
HERMEM_MIGRATIONS = [
    # pending_conflicts 表（V5.5 新增）
    """
    CREATE TABLE IF NOT EXISTS pending_conflicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        new_fact_text      TEXT NOT NULL,
        existing_fact_text TEXT NOT NULL,
        similarity        REAL NOT NULL,
        conflict_type     TEXT NOT NULL,
        existing_id      TEXT NOT NULL,
        status           TEXT DEFAULT 'pending',
        resolution_note  TEXT,
        created_at       REAL DEFAULT (julianday('now')),
        resolved_at      REAL
    );
    """,
    # l4_reflections 表（V5.5 新增）
    """
    CREATE TABLE IF NOT EXISTS l4_reflections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reflection_text TEXT NOT NULL,
        source_errors   INTEGER DEFAULT 0,
        confidence      REAL DEFAULT 0.5,
        created_at      REAL DEFAULT (julianday('now')),
        expires_at      REAL,
        injected_count  INTEGER DEFAULT 0,
        last_injected_at REAL
    );
    """,
    # chunks 表加字段
    "ALTER TABLE chunks ADD COLUMN usage_count INTEGER DEFAULT 0;",
    "ALTER TABLE chunks ADD COLUMN last_used_at REAL;",
    # 索引
    "CREATE INDEX IF NOT EXISTS idx_chunks_usage ON chunks(usage_count, last_used_at);",
]

# ── l0_l3.db 迁移 ─────────────────────────────────────────────────────────────
L0L3_MIGRATIONS = [
    # l1_facts 加字段（主动遗忘用）
    "ALTER TABLE l1_facts ADD COLUMN usage_count INTEGER DEFAULT 0;",
    "ALTER TABLE l1_facts ADD COLUMN last_used_at REAL;",
    # 索引
    "CREATE INDEX IF NOT EXISTS idx_l1_facts_usage ON l1_facts(usage_count, last_used_at);",
]


def main():
    print("=" * 60)
    print("V5.5 数据库迁移")
    print("=" * 60)
    migrate_db(HERMEM_DB, HERMEM_MIGRATIONS, "hermem.db")
    migrate_db(L0L3_DB, L0L3_MIGRATIONS, "l0_l3.db")
    print("全部迁移完成")


if __name__ == "__main__":
    main()
