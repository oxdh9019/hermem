#!/usr/bin/env python3
"""
Hermem Phase 3 - 数据库初始化
Step 0: 创建 l0_l3.db 及三张表（L1/L2/L3 staging）
"""
import sqlite3
from pathlib import Path

DB = Path.home() / ".hermes" / "memory" / "l0_l3.db"
DB.parent.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(DB)

# ── L1: 原子事实表 ──────────────────────────────────────────
conn.execute("""
CREATE TABLE IF NOT EXISTS l1_facts (
    id              TEXT PRIMARY KEY,
    l0_ref          TEXT NOT NULL,
    types           TEXT NOT NULL,
    type_confidence REAL DEFAULT 1.0,
    fallback_type   TEXT DEFAULT 'other',
    content         TEXT NOT NULL,
    tags            TEXT NOT NULL,
    value           TEXT NOT NULL,
    chunk_vector    BLOB NOT NULL,
    created_at      TEXT NOT NULL,
    status          TEXT DEFAULT 'active'
)
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_l1_status ON l1_facts(status)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_l1_l0     ON l1_facts(l0_ref)")

# ── L2: 场景聚合表 ─────────────────────────────────────────
conn.execute("""
CREATE TABLE IF NOT EXISTS l2_scenes (
    id               TEXT PRIMARY KEY,
    scene_type       TEXT NOT NULL,
    topic            TEXT NOT NULL,
    summary          TEXT NOT NULL,
    scene_embedding  BLOB NOT NULL,
    l1_refs          TEXT NOT NULL,
    occurrence_count INTEGER DEFAULT 1,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    status           TEXT DEFAULT 'active'
)
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_l2_status     ON l2_scenes(status)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_l2_last_seen ON l2_scenes(last_seen)")

# ── L3: Staging Area ──────────────────────────────────────
conn.execute("""
CREATE TABLE IF NOT EXISTS l3_staging (
    id          TEXT PRIMARY KEY,
    fact_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    confirmed   INTEGER DEFAULT 0
)
""")

conn.commit()
conn.close()

# Verify
conn2 = sqlite3.connect(DB)
tables = [r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
conn2.close()

print(f"✓ l0_l3.db initialized at {DB}")
print(f"  Tables: {', '.join(tables)}")
