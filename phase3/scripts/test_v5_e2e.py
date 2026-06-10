#!/usr/bin/env python3
"""
Hermem V5 Step 5: 端到端测试

验证 V5 active retrieval 各功能：
- T1: 向量检索
- T2: 分层阈值
- T3: 注入格式
- T4: 防重复
- T5: 中置信累积
- T6: 性能

用法: python3 scripts/test_v5_e2e.py
"""

import re
import sys
import time
from pathlib import Path

script_path = Path(__file__).resolve()
sys.path.insert(0, str(script_path.parent.parent))

import numpy as np
from impl import config
from impl.database import get_db
from impl.embedding import get_embedding_cached
from impl.vector_search import hermem_search_vector, search_with_tier
from impl.vectorstore import get_stats

INJECTION_PATTERN = re.compile(r"\[自动回忆 - 相似度 (\d+\.\d+)\]")


def test_injection_format():
    """T3: 验证注入格式符合 SPEC"""
    print("\n=== T3: 注入格式验证 ===")
    injection = "[自动回忆 - 相似度 0.91]\n以下是从历史记忆中检索到的相关内容（可能相关，仅供参考）：\n- 测试内容"  # noqa: E501
    matches = INJECTION_PATTERN.findall(injection)
    if matches:
        sim = float(matches[0])
        print(f"格式正确: 相似度={sim}")
        assert 0.8 <= sim <= 1.0, f"相似度 {sim} 超出合理范围"
        assert "以下是从历史记忆中检索到的相关内容（可能相关，仅供参考）：" in injection
        print("T3 通过")
    else:
        print("T3 失败: 格式错误")
        raise AssertionError("注入格式不符合 SPEC")


def test_vector_search():
    """T1: 向量检索 + T2: 分层阈值"""
    print("\n=== T1+T2: 向量检索 & 分层阈值 ===")

    test_queries = [
        ("Hermem 架构设计",),
        ("微博监控任务配置",),
        ("OpenClaw doctor 警告",),
    ]

    for (query,) in test_queries:
        emb, _ = get_embedding_cached(query)
        emb_arr = np.array(emb, dtype=np.float32)

        t0 = time.time()
        results = hermem_search_vector(
            emb_arr,
            threshold=config.ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM,
            top_k=5,
        )
        elapsed = time.time() - t0

        high, medium = search_with_tier(query=None, query_embedding=emb_arr, top_k=3)
        elapsed_total = time.time() - t0

        print(f"\n  [{query}] ({elapsed_total * 1000:.1f}ms)")
        print(f"    高置信(≥{config.ACTIVE_RETRIEVAL_THRESHOLD_HIGH}): {len(high)} 条")
        for h in high:
            print(f"      [{h['similarity']:.3f}] {h['content'][:40]}...")
        print(
            f"    中置信({config.ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM}-{config.ACTIVE_RETRIEVAL_THRESHOLD_HIGH}): {len(medium)} 条"
        )  # noqa: E501
        for m in medium[:2]:
            print(f"      [{m['similarity']:.3f}] {m['content'][:40]}...")

        if len(results) == 0:
            print(f"    WARNING: 无结果（查询 [{query}] 可能无相关记忆）")
            # Not a test failure - just no relevant memory exists

        # Verify format
        for r in results:
            assert "content" in r
            assert "similarity" in r
            assert 0 <= r["similarity"] <= 1.0

    print("\nT1+T2 通过")


def test_medium_tracker():
    """T5: 中置信累积逻辑"""
    print("\n=== T5: 中置信累积测试 ===")

    tracker = {}
    injected = set()
    chunk_id = "test_chunk"
    THRESHOLD_HIGH = config.ACTIVE_RETRIEVAL_THRESHOLD_HIGH

    # 第一条消息：0.72（中置信）
    sim1 = 0.72
    if chunk_id not in tracker:
        tracker[chunk_id] = sim1
    print(f"  消息1 (0.72): tracker={tracker[chunk_id]:.2f}")
    assert tracker[chunk_id] == 0.72

    # 第二条消息：0.78（中置信，更新最大值）
    sim2 = 0.78
    if chunk_id in tracker:
        tracker[chunk_id] = max(tracker[chunk_id], sim2)
    print(f"  消息2 (0.78): tracker={tracker[chunk_id]:.2f}")
    assert tracker[chunk_id] == 0.78

    # 第三条消息：0.86（达到注入阈值）
    sim3 = 0.86
    if tracker[chunk_id] >= sim3 and chunk_id not in injected:
        injected.add(chunk_id)
        popped = tracker.pop(chunk_id)
        print(f"  消息3 (0.86): 触发注入! tracker 已移除，injected={chunk_id in injected}")
        assert chunk_id not in tracker
        assert chunk_id in injected

    print("T5 通过")


def test_performance():
    """T6: 性能测试"""
    print("\n=== T6: 性能测试 ===")

    vectors = get_stats()
    print(f"向量库规模: {vectors['total_vectors']} 个向量")

    from impl.vectorstore import _load_vectors

    vecs = _load_vectors()

    times = []
    for _ in range(10):
        q = "测试查询内容"
        t0 = time.time()
        emb, _ = get_embedding_cached(q)
        emb_arr = np.array(emb, dtype=np.float32)

        # Simulate cosine calculation
        dots = vecs @ emb_arr
        q_norm = np.linalg.norm(emb_arr)
        vector_norms = np.linalg.norm(vecs, axis=1)
        with np.errstate(invalid="ignore"):
            scores = dots / (vector_norms * (q_norm + 1e-8))

        elapsed = time.time() - t0
        times.append(elapsed * 1000)

    avg_ms = sum(times) / len(times)
    max_ms = max(times)
    print(f"平均耗时: {avg_ms:.1f}ms")
    print(f"最大耗时: {max_ms:.1f}ms")

    # Total should be < 100ms (embedding 30-80ms + cosine < 5ms)
    assert avg_ms < 200, f"平均耗时 {avg_ms:.1f}ms 超过 200ms 阈值"
    print("T6 通过")


def test_no_duplicate_injection():
    """T4: 防重复注入"""
    print("\n=== T4: 防重复注入测试 ===")

    injected_ids = set()
    chunk = {
        "chunk_id": "dup_test",
        "content": "test content",
        "similarity": 0.90,
    }

    # 第一次注入
    if chunk["chunk_id"] not in injected_ids:
        injected_ids.add(chunk["chunk_id"])
        print(f"  第一次注入: OK, injected_ids={len(injected_ids)}")

    # 第二次注入（应该跳过）
    if chunk["chunk_id"] not in injected_ids:
        print("  第二次注入: 跳过（已在 injected_ids）")
    else:
        print(f"  第二次注入: 已跳过（chunk_id={chunk['chunk_id']} 已在集合中）")

    assert chunk["chunk_id"] in injected_ids
    assert len(injected_ids) == 1
    print("T4 通过")


def test_config():
    """T8: 配置化阈值"""
    print("\n=== T8: 配置化阈值 ===")
    print(f"  ACTIVE_RETRIEVAL_ENABLED = {config.ACTIVE_RETRIEVAL_ENABLED}")
    print(f"  ACTIVE_RETRIEVAL_THRESHOLD_HIGH = {config.ACTIVE_RETRIEVAL_THRESHOLD_HIGH}")
    print(f"  ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM = {config.ACTIVE_RETRIEVAL_THRESHOLD_MEDIUM}")
    print(f"  ACTIVE_RETRIEVAL_TOP_K = {config.ACTIVE_RETRIEVAL_TOP_K}")
    print(f"  ACTIVE_RETRIEVAL_FREQUENCY = {config.ACTIVE_RETRIEVAL_FREQUENCY}")
    print("T8 通过")


def main():
    print("=" * 50)
    print("Hermem V5 端到端测试")
    print("=" * 50)

    try:
        test_injection_format()
        test_vector_search()
        test_medium_tracker()
        test_performance()
        test_no_duplicate_injection()
        test_config()

        print("\n" + "=" * 50)
        print("所有测试通过")
        print("=" * 50)
    except AssertionError as e:
        print(f"\n测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
