"""Sprint 5+ 准备:每日 recall_outcome 数据快照(暂停观察 C 启动)。

每天用一次(或 cron),记录:
- recall_outcome 表行数
- l4_reflections 表行数
- chunks 总数 + last_used_at 非空数
- chunks 表按 chunk_type 分布

输出:Markdown 格式追加到 ~/.hermes/memory/eval/daily_snapshot.md
方便 30 天后看趋势。

Usage:
    cd phase3
    python3 scripts/daily_snapshot.py
"""

import datetime
import sqlite3
from pathlib import Path

HERMEM_DB = Path.home() / ".hermes" / "memory" / "hermem.db"
SNAPSHOT_PATH = Path.home() / ".hermes" / "memory" / "eval" / "daily_snapshot.md"


def collect_stats() -> dict:
    """从 hermem.db 收集统计。"""
    con = sqlite3.connect(str(HERMEM_DB))
    try:
        # 1. recall_outcome 总行数 + follow_up_type 分布
        ro_count = con.execute("SELECT COUNT(*) FROM recall_outcome").fetchone()[0]
        ro_by_type = {}
        for row in con.execute(
            "SELECT follow_up_type, COUNT(*) FROM recall_outcome GROUP BY follow_up_type"
        ).fetchall():
            ro_by_type[row[0] or "NULL"] = row[1]

        # 2. l4_reflections 总行数 + source_errors 分布
        l4_count = con.execute("SELECT COUNT(*) FROM l4_reflections").fetchone()[0]
        l4_by_source = {}
        for row in con.execute(
            "SELECT source_errors, COUNT(*) FROM l4_reflections GROUP BY source_errors"
        ).fetchall():
            l4_by_source[row[0]] = row[1]

        # 3. chunks 总数 + last_used_at 非空 + vec_index 非空
        chunks_total = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        chunks_with_used = con.execute(
            "SELECT COUNT(*) FROM chunks WHERE last_used_at IS NOT NULL"
        ).fetchone()[0]
        chunks_with_vec = con.execute(
            "SELECT COUNT(*) FROM chunks WHERE vec_index IS NOT NULL"
        ).fetchone()[0]

        # 4. chunks 按 type 分布
        by_type = {}
        for row in con.execute(
            "SELECT chunk_type, COUNT(*) FROM chunks GROUP BY chunk_type"
        ).fetchall():
            by_type[row[0] or "NULL"] = row[1]

        return {
            "recall_outcome_total": ro_count,
            "recall_outcome_by_type": ro_by_type,
            "l4_reflections_total": l4_count,
            "l4_reflections_by_source": l4_by_source,
            "chunks_total": chunks_total,
            "chunks_with_used": chunks_with_used,
            "chunks_with_vec": chunks_with_vec,
            "chunks_by_type": by_type,
        }
    finally:
        con.close()


def format_snapshot(stats: dict) -> str:
    """格式化为 Markdown。"""
    now = datetime.datetime.now()
    md = f"""## {now.strftime('%Y-%m-%d %H:%M')}

- **recall_outcome**: {stats['recall_outcome_total']} 行{', ' + str(stats['recall_outcome_by_type']) if stats['recall_outcome_by_type'] else ' (空)'}
- **l4_reflections**: {stats['l4_reflections_total']} 行{', ' + str(stats['l4_reflections_by_source']) if stats['l4_reflections_by_source'] else ' (空)'}
- **chunks**: {stats['chunks_total']} 总, last_used_at 非空 {stats['chunks_with_used']}, vec_index 非空 {stats['chunks_with_vec']}
- **chunks by type**: {stats['chunks_by_type']}

"""
    return md


def main():
    stats = collect_stats()
    md = format_snapshot(stats)
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 追加(不覆盖历史)
    if not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.write_text("# Hermem V6+ Daily Data Snapshot\n\n", encoding="utf-8")
    with SNAPSHOT_PATH.open("a", encoding="utf-8") as f:
        f.write(md)
    print(f"✓ 快照已追加: {SNAPSHOT_PATH}")
    print()
    print(md)


if __name__ == "__main__":
    main()
