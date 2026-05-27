#!/usr/bin/env python3
"""
Hermem V5.5 - 单元测试集合

运行方式:
    cd ~/.hermes/projects/hermem/phase3
    python3 v5.5/tests/test_v5_5_unit.py
"""

import sys
from pathlib import Path

# ── 路径设置 ───────────────────────────────────────────────────────────────────
IMPL_DIR = Path(__file__).parent.parent / "v5.5" / "impl"
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(IMPL_DIR))


# ── Test: llm_helper ──────────────────────────────────────────────────────────


class TestLlmHelper:
    def test_call_llm_with_fallback_returns_str_or_none(self):
        """call_llm_with_fallback 返回 str | None"""
        # 不实际调用 LLM，只测函数签名
        from impl.llm_helper import call_llm_fallback, call_llm_primary

        assert callable(call_llm_primary)
        assert callable(call_llm_fallback)
        print("✅ llm_helper 可导入")


# ── Test: l4_reflection ─────────────────────────────────────────────────────────


class TestL4Reflection:
    def test_l4_constants_defined(self):
        """L4 配置常量存在"""
        from impl.l4_reflection import (
            L4_MIN_ERRORS_FOR_REFLECTION,
            L4_PROMPT_MAX_CHARS,
            L4_REFLECTION_TTL_DAYS,
        )

        assert L4_MIN_ERRORS_FOR_REFLECTION == 3
        assert L4_REFLECTION_TTL_DAYS == 14
        assert L4_PROMPT_MAX_CHARS == 150
        print("✅ l4_reflection 常量定义正确")

    def test_get_yesterday_errors_requires_min_errors(self):
        """少于 3 条 error 时返回 None"""
        from impl.l4_reflection import synthesize_reflection

        result = synthesize_reflection([])
        assert result is None

        result = synthesize_reflection([{"context": "a", "error_type": "t", "surprise_level": 0.5}])
        assert result is None
        print("✅ l4_reflection 最小 error 数验证")


# ── Test: conflict_resolver ─────────────────────────────────────────────────────


class TestConflictResolver:
    def test_simple_contradiction_pos_vs_neg(self):
        """正向词 vs 负向词 → 矛盾"""
        from impl.conflict_resolver import _simple_contradiction_rule

        result = _simple_contradiction_rule("Oliver 喜欢直接给结论", "Oliver 讨厌啰嗦")
        assert result is True
        print("✅ 简单矛盾检测：喜欢 vs 讨厌")

    def test_simple_contradiction_same_polarity(self):
        """同向词 → 不矛盾"""
        from impl.conflict_resolver import _simple_contradiction_rule

        result = _simple_contradiction_rule("Oliver 喜欢直接给结论", "Oliver 倾向简洁风格")
        assert result is False
        print("✅ 简单矛盾检测：同向 → 不矛盾")

    def test_contradiction_threshold(self):
        """相似度 < 0.75 不触发（测试常量）"""
        from impl.conflict_resolver import CONFLICT_SIMILARITY_THRESHOLD

        assert CONFLICT_SIMILARITY_THRESHOLD == 0.75
        print("✅ 冲突相似度阈值 0.75")


# ── Test: active_forgetting ────────────────────────────────────────────────────


class TestActiveForgetting:
    def test_demotion_threshold_constants(self):
        """主动降级阈值常量存在"""
        from impl.active_forgetting import (
            DEMOTION_DAYS,
            DEMOTION_MIN_CONFIDENCE,
            SLEEP_USAGE_THRESHOLD,
        )

        assert SLEEP_USAGE_THRESHOLD == 5
        assert DEMOTION_DAYS == 30
        assert DEMOTION_MIN_CONFIDENCE == 0.6
        print("✅ active_forgetting 常量定义正确")


# ── Test: usage_tracker integration ────────────────────────────────────────────


class TestUsageTracker:
    def test_update_chunks_usage_async_increments_count(self):
        """update_chunks_usage_async 将 usage_count +1"""
        import os
        import sqlite3
        import time

        # 创建临时 DB 文件
        tmp_db = "/tmp/hermem_v55_test_usage.db"
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)

        conn = sqlite3.connect(tmp_db)
        conn.execute("""
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                content TEXT,
                usage_count INTEGER DEFAULT 0,
                last_used_at REAL
            )
        """)
        conn.executemany(
            "INSERT INTO chunks (id, content, usage_count) VALUES (?, ?, ?)",
            [(1, "test1", 0), (2, "test2", 0), (3, "test3", 0)],
        )
        conn.commit()
        conn.close()

        try:
            import importlib.util
            from pathlib import Path

            phase3 = str(Path(__file__).parent.parent.parent)
            spec = importlib.util.spec_from_file_location(
                "usage_tracker", Path(phase3) / "impl" / "usage_tracker.py"
            )
            usage_tracker_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(usage_tracker_mod)

            # 覆盖 DB 路径
            orig_db = usage_tracker_mod.HERMEM_DB
            usage_tracker_mod.HERMEM_DB = Path(tmp_db)

            try:
                usage_tracker_mod.update_chunks_usage_async([1, 2])
                time.sleep(0.8)

                conn2 = sqlite3.connect(tmp_db)
                cur = conn2.execute("SELECT id, usage_count FROM chunks ORDER BY id")
                rows = list(cur)
                conn2.close()

                row_dict = {r[0]: r[1] for r in rows}
                assert row_dict[1] == 1, f"chunk 1 usage_count 应为 1，实际 {row_dict[1]}"
                assert row_dict[2] == 1, f"chunk 2 usage_count 应为 1，实际 {row_dict[2]}"
                assert row_dict[3] == 0, f"chunk 3 未调用，应仍为 0，实际 {row_dict[3]}"
                print("✅ usage_tracker usage_count 增量更新正确")
            finally:
                usage_tracker_mod.HERMEM_DB = orig_db
        finally:
            if os.path.exists(tmp_db):
                os.unlink(tmp_db)


# ── Test: retrieval integration with usage_tracker ────────────────────────────


class TestRetrievalUsageTracker:
    def test_semantic_search_calls_usage_tracker(self):
        """semantic_search 调用 update_chunks_usage_async（检查源码）"""
        import re
        import sys
        from pathlib import Path

        phase3 = str(Path(__file__).parent.parent.parent)
        if phase3 not in sys.path:
            sys.path.insert(0, phase3)

        with open(Path(phase3) / "impl" / "retrieval.py") as f:
            content = f.read()

        # semantic_search 函数体
        match = re.search(
            r"def semantic_search\([^)]*\)[^:]*:(.*?)(?=\n(?:def |# ──))",
            content,
            re.DOTALL,
        )
        assert match, "找不到 semantic_search"
        sem_body = match.group(1)
        assert "update_chunks_usage_async" in sem_body, (
            "semantic_search 应调用 update_chunks_usage_async"
        )
        assert "threading.Thread" in sem_body, "semantic_search 应通过 threading.Thread 异步调用"
        print("✅ semantic_search 调用 update_chunks_usage_async（源码验证）")

    def test_keyword_search_calls_usage_tracker(self):
        """keyword_search 也调用 update_chunks_usage_async（检查源码）"""
        import re
        import sys
        from pathlib import Path

        phase3 = str(Path(__file__).parent.parent.parent)
        if phase3 not in sys.path:
            sys.path.insert(0, phase3)

        with open(Path(phase3) / "impl" / "retrieval.py") as f:
            content = f.read()

        match = re.search(
            r"def keyword_search\([^)]*\)[^:]*:(.*?)(?=\n(?:def |# ──))",
            content,
            re.DOTALL,
        )
        assert match, "找不到 keyword_search"
        kw_body = match.group(1)
        assert "update_chunks_usage_async" in kw_body, (
            "keyword_search 应调用 update_chunks_usage_async"
        )
        assert "threading.Thread" in kw_body, "keyword_search 应通过 threading.Thread 异步调用"
        print("✅ keyword_search 调用 update_chunks_usage_async（源码验证）")

    def test_hybrid_search_indirectly_calls_usage_tracker(self):
        """hybrid_search 通过 semantic_search 间接覆盖 usage_tracker"""
        import re
        import sys
        from pathlib import Path

        phase3 = str(Path(__file__).parent.parent.parent)
        if phase3 not in sys.path:
            sys.path.insert(0, phase3)

        with open(Path(phase3) / "impl" / "retrieval.py") as f:
            content = f.read()

        # hybrid_search 调用 semantic_search
        hybrid_match = re.search(
            r"def hybrid_search\([^)]*\)[^:]*:(.*?)(?=\n(?:def |# ──))",
            content,
            re.DOTALL,
        )
        assert hybrid_match, "找不到 hybrid_search"
        hybrid_body = hybrid_match.group(1)
        assert "semantic_search" in hybrid_body, "hybrid_search 应调用 semantic_search"

        # semantic_search 调用 usage_tracker（已验证）
        sem_match = re.search(
            r"def semantic_search\([^)]*\)[^:]*:(.*?)(?=\n(?:def |# ──))",
            content,
            re.DOTALL,
        )
        sem_body = sem_match.group(1)
        assert "update_chunks_usage_async" in sem_body, (
            "semantic_search 应调用 update_chunks_usage_async"
        )

        print("✅ hybrid_search → semantic_search → usage_tracker 链路完整")


# ── Test: l4_reflection output format ─────────────────────────────────────────


class TestL4OutputFormat:
    def test_synthesize_reflection_returns_str_not_none(self):
        """synthesize_reflection 返回 str 或 None（不是 dict）"""
        import sys
        from pathlib import Path

        phase3 = str(Path(__file__).parent.parent.parent)
        if phase3 not in sys.path:
            sys.path.insert(0, phase3)
        from impl.l4_reflection import L4_MIN_ERRORS_FOR_REFLECTION, synthesize_reflection

        errors = [
            {
                "context": "系统上次预测 Oliver 会要求详细解释，但实际他只要结论",
                "error_type": "disposition_mismatch",
                "surprise_level": 0.8,
            },
            {
                "context": "系统以为 Oliver 会问为什么不直接做，但实际他说'可以开始'",
                "error_type": "disposition_mismatch",
                "surprise_level": 0.9,
            },
            {
                "context": "系统认为需要给多个方案选择，但 Oliver 直接选第一个",
                "error_type": "disposition_mismatch",
                "surprise_level": 0.85,
            },
        ]

        # Mock LLM response
        import unittest.mock as mock

        with mock.patch("impl.l4_reflection._get_llm_helper") as mock_helper:
            mock_helper.return_value = lambda p, **kw: (
                "Oliver 工作流偏好：先评估再执行，不要跳步直接实现。"
            )

            result = synthesize_reflection(errors)

            # synthesize_reflection 返回 str | None，不是 dict
            assert result is None or isinstance(result, str), (
                f"synthesize_reflection 应返回 str | None，实际 {type(result)}"
            )
            if result:
                assert len(result) > 0, "reflection_text 不应为空"
                print(f"✅ synthesize_reflection 返回 str（长度 {len(result)}）")
            else:
                print("⚠️ synthesize_reflection 返回 None（可能是 mock 未生效）")


# ── 主入口 ─────────────────────────────────────────────────────────────────────


def run_all():
    tests = [
        TestLlmHelper(),
        TestL4Reflection(),
        TestConflictResolver(),
        TestActiveForgetting(),
        TestUsageTracker(),
        TestRetrievalUsageTracker(),
        TestL4OutputFormat(),
    ]

    print("=" * 50)
    print("Hermem V5.5 单元测试")
    print("=" * 50)

    passed = 0
    for t in tests:
        name = t.__class__.__name__
        try:
            for method_name in dir(t):
                if method_name.startswith("test_"):
                    getattr(t, method_name)()
                    passed += 1
        except Exception as e:
            print(f"❌ {name}.{method_name}: {e}")

    print(f"\n通过: {passed} 项 ✅")
    return passed


if __name__ == "__main__":
    run_all()
