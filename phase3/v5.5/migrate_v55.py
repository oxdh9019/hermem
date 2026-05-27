#!/usr/bin/env python3
"""
Hermem V5.5 - 数据库迁移脚本

运行一次，将以下改动应用到 hermem.db 和 l0_l3.db：
1. 新建 l4_reflections 表（如不存在）→ hermem.db
2. 新建 pending_conflicts 表（如不存在）→ hermem.db
3. 新建 prediction_errors 表（如不存在）→ hermem.db（LLM 反思层来源数据）
4. 给 chunks 加 usage_count, last_used_at 字段（如不存在）→ hermem.db
5. 给 l1_dispositions 加 archived, last_used_at, usage_count 字段（如不存在）→ l0_l3.db

用法: python3 phase3/v5.5/migrate_v55.py
"""

import sqlite3
from pathlib import Path

HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
L0L3_DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"

MIGRATIONS_HERMEM = [
    # l4_reflections 表
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
    # pending_conflicts 表
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
    # prediction_errors 表（V4.2 预测误差记录，L4 反思层来源）
    """
    CREATE TABLE IF NOT EXISTS prediction_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        context         TEXT NOT NULL,     -- 触发预测误差的对话上下文
        error_type      TEXT NOT NULL,     -- 误差类型：disposition_mismatch | recall_failure | ...
        surprise_level  REAL DEFAULT 0.5,  --惊讶程度 0-1
        created_at      REAL DEFAULT (julianday('now'))
    );
    """,
    # prediction_errors 日期索引
    "CREATE INDEX IF NOT EXISTS idx_pe_created ON prediction_errors(created_at);",
    # chunks 加 usage_count, last_used_at 字段
    "ALTER TABLE chunks ADD COLUMN usage_count INTEGER DEFAULT 0;",
    "ALTER TABLE chunks ADD COLUMN last_used_at REAL;",
    # 索引
    "CREATE INDEX IF NOT EXISTS idx_chunks_usage ON chunks(usage_count, last_used_at);",
]

MIGRATIONS_L0L3 = [
    # l1_dispositions 加 archived 字段
    "ALTER TABLE l1_dispositions ADD COLUMN archived INTEGER DEFAULT 0;",
    "ALTER TABLE l1_dispositions ADD COLUMN last_used_at REAL;",
    "ALTER TABLE l1_dispositions ADD COLUMN usage_count INTEGER DEFAULT 0;",
    # 索引
    "CREATE INDEX IF NOT EXISTS idx_l1_disp_archived ON l1_dispositions(archived);",
    "CREATE INDEX IF NOT EXISTS idx_l1_disp_last_used ON l1_dispositions(last_used_at);",
]


def _safe_execute(cur, sql, name):
    """执行单条 SQL，忽略已存在的错误"""
    try:
        cur.execute(sql)
        print(f"  ✅ {name}")
        return True
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "duplicate column" in msg or "already exists" in msg or "duplicate index" in msg:
            print("  ⚠️  已存在，跳过")
            return True
        else:
            print(f"  ❌ 错误: {e}")
            return False
    except Exception as e:
        print(f"  ❌ 错误: {e}")
        return False


def _migrate(db_path: Path, migrations: list, db_name: str):
    if not db_path.exists():
        print(f"  ⚠️  {db_name} 不存在，跳过")
        return

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    print(f"连接数据库: {db_path}")

    for item in migrations:
        # 支持 (sql, optional=True) 元组 或 纯字符串
        if isinstance(item, tuple):
            sql, optional = item
        else:
            sql, optional = item, False

        sql_upper = sql.upper().strip()
        if "CREATE TABLE" in sql_upper:
            name = sql.split("CREATE TABLE IF NOT EXISTS ")[-1].split(" ")[0]
        elif "ALTER TABLE" in sql_upper:
            name = sql.split("ALTER TABLE ")[-1].split(" ")[0]
            parts = sql.split(",")
            for p in parts:
                if "ADD COLUMN" in p.upper():
                    name = name + "+" + p.split("ADD COLUMN")[-1].strip().split(" ")[0]
                    break
        elif "CREATE INDEX" in sql_upper:
            name = "idx:" + sql.split("CREATE INDEX IF NOT EXISTS ")[-1].split(" ")[0]
        else:
            name = "op"

        ok = _safe_execute(cur, sql, name)
        if not ok and not optional:
            conn.close()
            raise sqlite3.OperationalError(f"迁移失败: {sql[:50]}")

    conn.commit()
    conn.close()


def migrate():
    print("=" * 50)
    print("Hermem V5.5 数据库迁移")
    print("=" * 50)

    print("\n[1/2] hermem.db 迁移...")
    _migrate(HERMEM_DB, MIGRATIONS_HERMEM, "hermem.db")

    print("\n[2/2] l0_l3.db 迁移...")
    _migrate(L0L3_DB, MIGRATIONS_L0L3, "l0_l3.db")

    print("\n迁移完成 ✅")


if __name__ == "__main__":
    migrate()
