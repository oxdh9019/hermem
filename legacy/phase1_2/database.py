"""Hermem Phase 2 - SQLite 数据库层。

职责：
- hermem.db 的初始化和连接管理
- chunks 表的 CRUD 操作
- embedding_cache 表的操作
- FTS5 索引维护
"""

import sqlite3
import json
import os
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

# ── 路径配置 ────────────────────────────────────────────
HERMEM_DIR = Path.home() / ".hermes" / "memory"
HERMEM_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = HERMEM_DIR / "hermem.db"


# ── 数据库连接（单例） ───────────────────────────────────
_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    """获取数据库连接（延迟初始化，单例）。"""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.row_factory = sqlite3.Row
    return _conn


@contextmanager
def get_db():
    """线程安全的数据库上下文管理器。"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def close_conn():
    """关闭数据库连接（通常在进程退出时调用）。"""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# ── 初始化 ──────────────────────────────────────────────
INIT_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    chunk_type  TEXT    NOT NULL,
    concepts    TEXT,
    created_at  REAL    DEFAULT (julianday('now')),
    source_file TEXT,
    source_line INTEGER,
    vec_index   INTEGER
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    text_hash  TEXT PRIMARY KEY,
    embedding  BLOB,
    created_at REAL DEFAULT (julianday('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    content=chunks,
    content_rowid=id
);

CREATE INDEX IF NOT EXISTS idx_chunks_vec_index  ON chunks(vec_index);
CREATE INDEX IF NOT EXISTS idx_chunks_session    ON chunks(session_id);
CREATE INDEX IF NOT EXISTS idx_chunks_type      ON chunks(chunk_type);
CREATE INDEX IF NOT EXISTS idx_chunks_created   ON chunks(created_at);
"""


def init_db():
    """初始化数据库（创建表和索引）。幂等操作，重复调用安全。"""
    conn = get_conn()
    conn.executescript(INIT_SQL)
    conn.commit()


# ── Chunks 表操作 ───────────────────────────────────────

def insert_chunk(
    session_id: str,
    content: str,
    chunk_type: str,
    concepts: list[str],
    source_file: Optional[str] = None,
    source_line: Optional[int] = None,
    vec_index: Optional[int] = None,
) -> int:
    """插入一条记忆片段。

    Returns:
        chunk_id (int): 新插入记录的 id。
    """
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO chunks
                (session_id, content, chunk_type, concepts, source_file, source_line, vec_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            content,
            chunk_type,
            json.dumps(concepts, ensure_ascii=False),
            source_file,
            source_line,
            vec_index,
        ))
        chunk_id = cur.lastrowid

        # 同步 FTS5 索引
        conn.execute(
            "INSERT INTO chunks_fts(rowid, content) VALUES (?, ?)",
            (chunk_id, content),
        )

    return chunk_id


def get_chunk_by_id(chunk_id: int) -> Optional[dict]:
    """根据 id 查询单条记忆。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)


def get_chunks_by_session(session_id: str) -> list[dict]:
    """获取某个会话的所有记忆片段。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE session_id = ? ORDER BY created_at",
            (session_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_chunks_by_type(chunk_type: str, limit: int = 100) -> list[dict]:
    """按类型查询记忆（如 'session_summary'、'concept_note'）。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE chunk_type = ? ORDER BY created_at DESC LIMIT ?",
            (chunk_type, limit)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def search_chunks_by_text(keyword: str, limit: int = 20) -> list[dict]:
    """根据关键词搜索记忆（LIKE 模糊匹配，用于简单回退）。"""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM chunks
            WHERE content LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (f"%{keyword}%", limit)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def delete_chunk(chunk_id: int):
    """删除单条记忆。FTS 记录会通过 content=chunks 自动级联删除。"""
    with get_db() as conn:
        conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))


def get_chunk_count() -> int:
    """返回 chunks 表总记录数。"""
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return row[0] if row else 0


# ── Embedding Cache ──────────────────────────────────────

def get_cached_embedding(text_hash: str) -> Optional[bytes]:
    """查询缓存的 embedding（返回原始 BLOB）。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT embedding FROM embedding_cache WHERE text_hash = ?",
            (text_hash,)
        ).fetchone()
        return row[0] if row else None


def set_cached_embedding(text_hash: str, embedding_blob: bytes):
    """写入 embedding 缓存。"""
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO embedding_cache (text_hash, embedding, created_at)
            VALUES (?, ?, julianday('now'))
        """, (text_hash, embedding_blob))


def get_cache_stats() -> dict:
    """返回缓存统计信息。"""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]
        return {"total_entries": total}


# ── 工具函数 ────────────────────────────────────────────

def rows_to_dicts(rows) -> list[dict]:
    """将 fetchall() 结果批量转为 dict 列表。兼容 sqlite3.Row 和普通 tuple。"""
    return [dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    """将 sqlite3.Row 转为普通 dict（便于序列化）。"""
    return dict(row)


def chunks_table_info() -> list[dict]:
    """返回 chunks 表的列信息（用于调试）。"""
    with get_db() as conn:
        rows = conn.execute("PRAGMA table_info(chunks)").fetchall()
        return [_row_to_dict(r) for r in rows]
