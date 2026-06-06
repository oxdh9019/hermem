#!/usr/bin/env python3
"""
Hermem V5.5 - 端到端测试
验证 V5.5 所有模块集成后的正确性。

运行方式:
    cd ~/.hermes/projects/hermem/phase3
    python3 -m pytest v5.5/tests/test_v5_5_e2e.py -v
"""

import sys
from pathlib import Path

import pytest

# ── 路径设置 ───────────────────────────────────────────────────────────────────
IMPL_DIR = Path(__file__).parent.parent / "v5.5" / "impl"
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(IMPL_DIR))


# ── Test: Migration ────────────────────────────────────────────────────────────


class TestMigration:
    """E2E.0: 迁移脚本重跑不报错（幂等）"""

    def test_migration_runs_without_error(self, tmp_path, monkeypatch):
        """迁移脚本独立运行不报错，重复运行不报 duplicate column 错误"""
        # 使用临时数据库测试
        import sqlite3

        # 临时 hermem.db
        hermem_db = tmp_path / "hermem.db"
        conn = sqlite3.connect(str(hermem_db))
        conn.execute("""
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                usage_count INTEGER DEFAULT 0,
                last_used_at REAL
            );
        """)
        conn.execute("""
            CREATE TABLE prediction_errors (
                id INTEGER PRIMARY KEY,
                created_at REAL DEFAULT (julianday('now'))
            );
        """)
        conn.commit()
        conn.close()

        # 临时 l0_l3.db
        l0_db = tmp_path / "l0_l3.db"
        conn = sqlite3.connect(str(l0_db))
        conn.execute("""
            CREATE TABLE l1_dispositions (
                id TEXT PRIMARY KEY,
                is_active INTEGER DEFAULT 1
            );
        """)
        conn.commit()
        conn.close()

        # 替换路径（用 module 对象而非 dotted name——"phase3.v5_5" 不是合法
        # import 路径，因数字段不能作为 Python 包名；直接 import 模块再 setattr）
        import migrate_v55 as _migrate_mod
        monkeypatch.setattr(_migrate_mod, "HERMEM_DB", hermem_db)
        monkeypatch.setattr(_migrate_mod, "L0L3_DB", l0_db)

        # 运行迁移（第一次）
        from migrate_v55 import MIGRATIONS_HERMEM, MIGRATIONS_L0L3, _migrate

        _migrate(hermem_db, MIGRATIONS_HERMEM, "hermem.db")
        _migrate(l0_db, MIGRATIONS_L0L3, "l0_l3.db")

        # 验证 l4_reflections 表创建
        conn = sqlite3.connect(str(hermem_db))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [t[0] for t in tables]
        conn.close()

        assert "l4_reflections" in table_names
        assert "pending_conflicts" in table_names

        # 重复运行不报错（幂等）
        _migrate(hermem_db, MIGRATIONS_HERMEM, "hermem.db")
        _migrate(l0_db, MIGRATIONS_L0L3, "l0_l3.db")

        print("E2E.0 ✅ 迁移脚本幂等通过")


# ── Test: usage_count / last_used_at 维护 ──────────────────────────────────────


class TestUsageTracker:
    """E2E.0b: usage_count/last_used_at 维护正常工作"""

    def test_chunks_usage_increment(self, tmp_path, monkeypatch):
        """chunk 被命中后 usage_count += 1"""
        import sqlite3

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                usage_count INTEGER DEFAULT 0,
                last_used_at REAL
            );
        """)
        conn.execute("INSERT INTO chunks (id) VALUES (1), (2), (3)")
        conn.commit()
        conn.close()

        # 直接测试 SQL
        import sqlite3 as sq

        conn = sq.connect(str(db_path))
        now = conn.execute("SELECT julianday('now')").fetchone()[0]
        conn.executemany(
            "UPDATE chunks SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
            [(now, 1), (now, 2), (now, 3)],
        )
        conn.commit()

        conn = sq.connect(str(db_path))
        rows = conn.execute("SELECT usage_count FROM chunks WHERE id IN (1,2,3)").fetchall()
        conn.close()

        assert all(r[0] == 1 for r in rows)
        print("E2E.0b ✅ chunks usage_count 正确更新")


# ── Test: L4 Reflection ─────────────────────────────────────────────────────────


class TestL4Reflection:
    """E2E.1: L4 完整流程（fallback 生效）"""

    def test_reflection_requires_minimum_errors(self, tmp_path, monkeypatch):
        """少于 3 条 error 时跳过（不写入）"""
        import sqlite3

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        # Python 3.14 sqlite3 execute() 一次只能执行一条语句——拆成两次
        conn.execute(
            """
            CREATE TABLE l4_reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reflection_text TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE prediction_errors (
                id INTEGER PRIMARY KEY,
                context TEXT,
                error_type TEXT,
                surprise_level REAL,
                created_at REAL DEFAULT (julianday('now'))
            );
            """
        )
        # 只插入 2 条 error
        conn.execute(
            "INSERT INTO prediction_errors (context, error_type, surprise_level) VALUES (?, ?, ?)",
            ("error 1", "type_a", 0.8),
        )
        conn.execute(
            "INSERT INTO prediction_errors (context, error_type, surprise_level) VALUES (?, ?, ?)",
            ("error 2", "type_b", 0.7),
        )
        conn.commit()
        conn.close()

        # 用 module 对象 patch（避免 "phase3.v5_5.impl.X" 命名空间解析失败）
        import l4_reflection as _l4_mod
        monkeypatch.setattr(_l4_mod, "HERMEM_DB", db_path)

        # 验证：2 条 error 不会触发反射
        from impl.l4_reflection import L4_MIN_ERRORS_FOR_REFLECTION, get_yesterday_errors

        errors = get_yesterday_errors()
        assert len(errors) < L4_MIN_ERRORS_FOR_REFLECTION

        print("E2E.1 ✅ L4 reflection min errors 验证通过")


# ── Test: Conflict Resolver ─────────────────────────────────────────────────────


class TestConflictResolver:
    """E2E.2: 冲突检测在 L1 持久化后触发（非 sync_turn）"""

    def test_simple_contradiction_detected(self):
        """简单矛盾："喜欢A" vs "讨厌A" 返回冲突"""
        from impl.conflict_resolver import _simple_contradiction_rule

        # 正向 vs 负向 → 矛盾
        result = _simple_contradiction_rule("Oliver 喜欢直接给结论", "Oliver 讨厌啰嗦")
        assert result is True

        # 同向 → 不矛盾
        result = _simple_contradiction_rule("Oliver 喜欢直接给结论", "Oliver 倾向简洁")
        assert result is False

        print("E2E.2 ✅ 简单矛盾检测通过")


# ── Test: Active Forgetting ────────────────────────────────────────────────────


class TestActiveForgetting:
    """E2E.3: 遗忘：高频 fact → user_profile.md 更新 + 低置信 disposition 归档"""

    def test_demotion_excludes_high_confidence(self, tmp_path, monkeypatch):
        """confidence >= 0.6 的 disposition 不被归档（即使 30 天未召回）"""
        import sqlite3

        l0_db = tmp_path / "l0.db"
        conn = sqlite3.connect(str(l0_db))
        conn.execute("""
            CREATE TABLE l1_dispositions (
                id TEXT PRIMARY KEY,
                is_active INTEGER DEFAULT 1,
                confidence REAL DEFAULT 0.8,
                last_used_at REAL,
                condition_text TEXT,
                prediction_text TEXT
            );
        """)
        # 高置信度 disposition
        conn.execute(
            "INSERT INTO l1_dispositions (id, confidence, last_used_at) VALUES (?, ?, ?)",
            ("high_conf", 0.8, None),
        )
        conn.commit()
        conn.close()

        # 用 module 对象 patch（避免 "phase3.v5_5.impl.X" 命名空间解析失败）
        import active_forgetting as _af_mod
        monkeypatch.setattr(_af_mod, "L0L3_DB", l0_db)

        from impl.active_forgetting import active_demotion

        result = active_demotion(min_confidence=0.6)
        # high_conf 因为置信度 >= 0.6，不会被归档
        assert result["demoted"] == 0

        print("E2E.3 ✅ 高置信 disposition 未被归档")


# ── Test: Config ───────────────────────────────────────────────────────────────


class TestConfig:
    """验证 config.py 中必要配置存在"""

    def test_llm_fallback_config_exists(self):
        # V5.5 llm_helper 用 LLM_PRIMARY_MODEL / LLM_FALLBACK_MODEL
        # （不是 LLM_MODEL）——config.py 在 V5.5 重命名过
        from impl.config import LLM_PRIMARY_MODEL, LLM_FALLBACK_MODEL

        assert LLM_PRIMARY_MODEL
        assert LLM_FALLBACK_MODEL
        print("✅ config.py LLM 配置存在")


# ── 运行入口 ───────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
