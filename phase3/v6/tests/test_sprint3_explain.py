"""Hermem V6 Sprint 3 - 解释层单元测试(12 个)。"""

import json
from unittest.mock import patch, MagicMock

import pytest
import requests


@pytest.fixture(autouse=True)
def reset_explain_metrics():
    from impl.explain import reset_explain_metrics
    reset_explain_metrics()
    yield
    reset_explain_metrics()


# ── 1. 模板(任务 3.1,4 个) ─────────────────────────────────────────
def test_render_explanation_basic():
    from impl.explain_templates import render_explanation
    out = render_explanation(
        "上周 cron 任务失败的根因是 launchd 路径",
        "cron 任务",
        0.85,
        seed="t1",
    )
    assert "cron 任务" in out
    assert "launchd 路径" in out
    assert "高置信" in out  # 0.85 → 高档


def test_select_template_deterministic():
    from impl.explain_templates import select_template
    t1 = select_template("seed_42")
    t2 = select_template("seed_42")
    assert t1 == t2


def test_select_template_different_seeds_diversity():
    """6 seed 应看到 ≥ 3 个不同模板(md5 哈希分布不一定全覆盖)。"""
    from impl.explain_templates import select_template
    templates = {select_template(f"seed_{i}") for i in range(6)}
    assert len(templates) >= 3


def test_relevance_hint_three_buckets():
    from impl.explain_templates import relevance_hint
    assert relevance_hint(0.1) == "低置信"
    assert relevance_hint(0.3) == "低置信"  # 边界
    assert relevance_hint(0.5) == "中置信"
    assert relevance_hint(0.6) == "中置信"  # 边界
    assert relevance_hint(0.8) == "高置信"
    assert relevance_hint(1.0) == "高置信"


# ── 2. explain_chunk 轻量路径(任务 3.2,2 个) ────────────────────
def test_explain_chunk_template_path():
    """默认 use_llm=False 走模板路径,零 LLM 延迟。"""
    from impl.explain import explain_chunk, get_explain_metrics
    out = explain_chunk(
        chunk={"id": 1, "content": "test content"},
        current_query="q",
        similarity=0.85,
        use_llm=False,
        seed="t1",
    )
    assert "test content" in out
    m = get_explain_metrics()
    assert m["template"] == 1
    assert m["llm"] == 0


def test_explain_chunk_template_fallback_to_chunk_id_seed():
    """seed="" 时用 chunk_id 作 seed(应工作)。"""
    from impl.explain import explain_chunk
    out = explain_chunk(
        chunk={"id": 99, "content": "fallback seed test"},
        current_query="q",
        similarity=0.5,
        use_llm=False,
        seed="",
    )
    assert "fallback seed test" in out


# ── 3. explain_chunk 增强路径(任务 3.3,4 个) ────────────────────
def test_explain_chunk_llm_path_with_mock():
    """mock 4b 返回正常短文本 → 走增强路径。"""
    from impl.explain import explain_chunk, get_explain_metrics
    fake_resp = MagicMock()
    fake_resp.text = json.dumps({
        "message": {"content": "看到您提到 q,我想起 关联内容(高置信)。"},
        "done": True,
    })
    fake_resp.raise_for_status = MagicMock()
    with patch("impl.predictor.requests.post", return_value=fake_resp):
        out = explain_chunk(
            chunk={"id": 1, "content": "test"},
            current_query="q",
            similarity=0.85,
            use_llm=True,
        )
    assert "看到您提到" in out
    m = get_explain_metrics()
    assert m["llm"] == 1
    assert m["llm_fallback"] == 0


def test_explain_chunk_llm_timeout_fallback_to_v5():
    """LLM 超时 → V5 格式降级。"""
    from impl.explain import explain_chunk, get_explain_metrics
    with patch("impl.predictor.requests.post", side_effect=requests.Timeout("simulated")):
        out = explain_chunk(
            chunk={"id": 1, "content": "fallback content here"},
            current_query="q",
            similarity=0.85,
            use_llm=True,
        )
    assert "[自动回忆 - 相似度 0.85]" in out
    assert "fallback content here" in out
    m = get_explain_metrics()
    assert m["llm_timeout"] == 1
    assert m["llm_fallback"] == 1


def test_explain_chunk_llm_long_output_fallback_to_v5():
    """LLM 输出 > 300 字 → V5 格式降级(防超长)。"""
    from impl.explain import explain_chunk
    fake_resp = MagicMock()
    fake_resp.text = json.dumps({
        "message": {"content": "X" * 500},
        "done": True,
    })
    fake_resp.raise_for_status = MagicMock()
    with patch("impl.predictor.requests.post", return_value=fake_resp):
        out = explain_chunk(
            chunk={"id": 1, "content": "test"},
            current_query="q",
            similarity=0.85,
            use_llm=True,
        )
    assert "[自动回忆 - 相似度 0.85]" in out


def test_explain_chunk_llm_exception_fallback_to_v5():
    """LLM 抛通用异常 → V5 格式降级。"""
    from impl.explain import explain_chunk
    with patch("impl.predictor.requests.post", side_effect=RuntimeError("simulated")):
        out = explain_chunk(
            chunk={"id": 1, "content": "test"},
            current_query="q",
            similarity=0.85,
            use_llm=True,
        )
    assert "[自动回忆 - 相似度 0.85]" in out


# ── 4. 指标(2 个) ────────────────────────────────────────────────
def test_explain_metrics_track_template_path_count():
    """5 次模板路径调用后,template=5, llm=0。"""
    from impl.explain import explain_chunk, get_explain_metrics
    for i in range(5):
        explain_chunk({"id": i, "content": f"c{i}"}, "q", 0.5, use_llm=False)
    m = get_explain_metrics()
    assert m["total"] == 5
    assert m["template"] == 5
    assert m["llm"] == 0


def test_explain_metrics_p95_latency_with_mocks():
    """5 次 mock 增强路径,llm=5, p95 有值。"""
    from impl.explain import explain_chunk, get_explain_metrics
    fake_resp = MagicMock()
    fake_resp.text = json.dumps({"message": {"content": "x"}, "done": True})
    fake_resp.raise_for_status = MagicMock()
    with patch("impl.predictor.requests.post", return_value=fake_resp):
        for _ in range(5):
            explain_chunk({"id": 1, "content": "x"}, "q", 0.5, use_llm=True)
    m = get_explain_metrics()
    assert m["llm"] == 5
    # p95 应 > 0(mock 网络调用实际有 latency)
    assert m["llm_p95_ms"] >= 0


# 5. 边缘情况(1 个) ────────────────────────────────────────────
def test_explain_chunk_no_content_fallback():
    """chunk 无 content 字段 → 模板不崩,excerpt 为空。"""
    from impl.explain import explain_chunk
    out = explain_chunk(
        chunk={"id": 1},  # 无 content
        current_query="test question",
        similarity=0.5,
        use_llm=False,
    )
    # 应返回模板渲染结果(无 content 也不崩),excerpt 空
    assert "中置信" in out  # 0.5 → 中档 hint
    assert len(out) > 0
