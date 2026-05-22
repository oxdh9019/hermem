#!/usr/bin/env python3
"""
一次性迁移：为旧版 l1_dispositions 添加 V4.3+ 所需的列。

安全：ALTER TABLE ADD COLUMN 对已存在的列会报错，用 PRAGMA table_info 检查。
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".hermes" / "memory" / "l0_l3.db"

COLUMNS_TO_ADD = [
    ("source_agent",         "TEXT",              None),
    ("scope",               "TEXT",              "'model_error'"),
    ("weight",              "REAL",              "1.0"),
    ("intent",              "TEXT",              None),
    # error_type/keywords/source_session_id 三个 V4.2 列，防旧 DB 缺失
    ("error_type",           "TEXT",              None),
    ("keywords",            "TEXT",              None),
    ("source_session_id",    "TEXT",              None),
]


def migrate():
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(l1_dispositions)")
    existing = {r[1] for r in cursor.fetchall()}

    added = []
    for col_name, col_type, default_val in COLUMNS_TO_ADD:
        if col_name not in existing:
            default_clause = f" DEFAULT {default_val}" if default_val else ""
            sql = f"ALTER TABLE l1_dispositions ADD COLUMN {col_name} {col_type}{default_clause}"
            cursor.execute(sql)
            added.append(col_name)
            print(f"  + {col_name} ({col_type}{default_clause})")
        else:
            print(f"  = {col_name} (already exists)")

    if added:
        conn.commit()
        print(f"\n✓ Added {len(added)} columns: {', '.join(added)}")
    else:
        print("\n✓ All columns already exist, nothing to migrate")

    # Verify final schema
    cursor.execute("PRAGMA table_info(l1_dispositions)")
    final_cols = sorted([r[1] for r in cursor.fetchall()])
    print(f"\n  Final schema: {len(final_cols)} columns")
    conn.close()


if __name__ == "__main__":
    migrate()
