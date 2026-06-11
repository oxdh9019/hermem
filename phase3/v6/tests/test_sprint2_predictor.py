"""Hermem V6 Sprint 2 — 预测性召回单元测试。

18 个测试,7 类:
1. prompt 工程(2)
2. LLM 调用(3)
3. 解析容错(4)
4. 主函数(2)
5. 整合(3)
6. 失败降级(2)
7. 桥层 e2e(2)
"""

import json
import time
from unittest.mock import patch, MagicMock

import pytest
import requests


# ── 模块级 fixture ──────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def reset_metrics():
    """每个测试前重置指标,避免测试间干扰。"""
    from impl.predictor import reset_predictor_metrics
    reset_predictor_metrics()
    yield
    reset_predictor_metrics()


# ── 1. prompt 工程(2 个) ─────────────────────────────────────────
def test_build_predictive_prompt_with_full_context():
    """画像 + user_query → 完整 prompt(包含画像、query、few-shot examples)。"""
    from impl.predictor import build_predictive_prompt
    p = build_predictive_prompt(
        user_profile="用户: Oliver, 偏好简洁",
        user_query="hermem V6 进度",
    )
    assert "用户: Oliver, 偏好简洁" in p
    assert "hermem V6 进度" in p
    assert "示例 1" in p  # few-shot 存在
    assert "V6 Sprint 2 实现细节" in p  # few-shot 内容


def test_build_predictive_prompt_truncates_long_query():
    """user_query > 300 字 → 截断到 300。"""
    from impl.predictor import build_predictive_prompt
    long_query = "X" * 500
    p = build_predictive_prompt(user_profile="", user_query=long_query)
    # 检查 300 个 X 之后,不应再有第 301 个连续的 X
    assert "X" * 301 not in p


# ── 2. LLM 调用(3 个) ───────────────────────────────────────────
def test_call_predictor_llm_returns_text():
    """正常调用 → 返回 LLM 文本(4b 模式:done=true 行有完整 content)。"""
    from impl.predictor import call_predictor_llm, LLM_MODEL, LLM_TIMEOUT_S
    assert LLM_MODEL == "qwen3.5:4b-no-think"
    assert LLM_TIMEOUT_S == 5.0  # 复核修订:5s 给 cold 100% 覆盖(Sprint 4 修偏差 2)
    # 跳过网络调用:用 mock
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = json.dumps({
        "message": {"content": "Q1\nQ2\nQ3"},
        "done": True,
    })
    fake_resp.raise_for_status = MagicMock()
    with patch("impl.predictor.requests.post", return_value=fake_resp):
        result = call_predictor_llm("test prompt")
    assert result == "Q1\nQ2\nQ3"


def test_call_predictor_llm_raises_on_timeout():
    """requests.Timeout → 透传(不 catch,让 generate_predictive_queries 兜底)。"""
    from impl.predictor import call_predictor_llm
    with patch("impl.predictor.requests.post", side_effect=requests.Timeout("simulated")):
        with pytest.raises(requests.Timeout):
            call_predictor_llm("test", timeout=0.1)


def test_call_predictor_llm_uses_correct_model_and_think_false():
    """验证 payload 用 4b 模型 + think=False(防止 qwen3.5 tag 模型仍 think)。"""
    from impl.predictor import call_predictor_llm
    fake_resp = MagicMock()
    fake_resp.text = json.dumps({"message": {"content": "ok"}, "done": True})
    fake_resp.raise_for_status = MagicMock()
    with patch("impl.predictor.requests.post", return_value=fake_resp) as mock_post:
        call_predictor_llm("test")
    call_args = mock_post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["model"] == "qwen3.5:4b-no-think"
    assert payload["think"] is False
    assert payload["stream"] is False
    assert payload["options"]["num_predict"] <= 100  # 不会太长


# ── 3. 解析容错(4 个) ───────────────────────────────────────────
def test_parse_llm_output_normal_lines():
    from impl.predictor import _parse_llm_output
    assert _parse_llm_output("query1\nquery2\nquery3") == ["query1", "query2", "query3"]


def test_parse_llm_output_numbered():
    from impl.predictor import _parse_llm_output
    assert _parse_llm_output("1. xxx\n2. yyy") == ["xxx", "yyy"]


def test_parse_llm_output_with_bullets():
    from impl.predictor import _parse_llm_output
    assert _parse_llm_output("- aaa\n- bbb") == ["aaa", "bbb"]
    assert _parse_llm_output("* ccc\n* ddd") == ["ccc", "ddd"]


def test_parse_llm_output_empty_returns_empty():
    from impl.predictor import _parse_llm_output
    assert _parse_llm_output("") == []
    assert _parse_llm_output("\n\n\n") == []
    # 全部超长行 → 空
    assert _parse_llm_output("X" * 100) == []


# ── 4. 主函数(2 个) ─────────────────────────────────────────────
def test_generate_predictive_queries_returns_2_to_3(monkeypatch):
    """mock LLM 返回 3 行 → generate 返回 3 个查询词。"""
    from impl import predictor
    fake_resp = MagicMock()
    fake_resp.text = json.dumps({
        "message": {"content": "V6 Sprint 2 详情\n风险点\n下一步"},
        "done": True,
    })
    fake_resp.raise_for_status = MagicMock()
    monkeypatch.setattr("impl.predictor.requests.post", lambda *a, **kw: fake_resp)
    qs = predictor.generate_predictive_queries(user_profile="test", user_query="hermem")
    assert len(qs) == 3
    assert qs == ["V6 Sprint 2 详情", "风险点", "下一步"]


def test_generate_predictive_queries_falls_back_on_timeout(monkeypatch):
    """LLM 超时 → 返回 []。"""
    from impl import predictor
    monkeypatch.setattr(
        "impl.predictor.requests.post",
        lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout("simulated")),
    )
    qs = predictor.generate_predictive_queries(user_profile="", user_query="test")
    assert qs == []


# ── 5. 整合(3 个) ───────────────────────────────────────────────
def test_search_predictive_returns_explicit_when_predictor_empty(monkeypatch):
    """预测器返回 [] → 仅显式结果(不调 search_with_tier 第二次)。"""
    from impl import predictor
    monkeypatch.setattr(predictor, "generate_predictive_queries", lambda *a, **kw: [])
    # 显式 search_with_tier 也 mock
    monkeypatch.setattr(
        predictor,
        "search_with_tier",
        lambda query, top_k, **kw: ([{"id": 1, "content": "explicit"}], []),
    )
    high, medium = predictor.search_predictive("test", "profile")
    assert len(high) == 1
    assert high[0]["id"] == 1


def test_search_predictive_fuses_explicit_and_predicted(monkeypatch):
    """预测有结果 → 显式+预测融合(应包含两路 chunk)。"""
    from impl import predictor
    monkeypatch.setattr(
        predictor,
        "generate_predictive_queries",
        lambda *a, **kw: ["predicted_query"],
    )
    # 显式返回 id=1, 预测返回 id=2
    def fake_search_with_tier(query, top_k, **kw):
        if query == "explicit":
            return ([{"id": 1, "content": "explicit_chunk"}], [])
        return ([{"id": 2, "content": "predicted_chunk"}], [])
    monkeypatch.setattr(predictor, "search_with_tier", fake_search_with_tier)
    high, medium = predictor.search_predictive("explicit", "profile")
    ids = {c["id"] for c in high}
    assert 1 in ids  # 显式
    assert 2 in ids  # 预测


def test_search_predictive_dedupes_overlapping_chunks(monkeypatch):
    """显式+预测都命中同一 chunk → RRF 合并为 1 个(分数相加)。"""
    from impl import predictor
    monkeypatch.setattr(
        predictor, "generate_predictive_queries", lambda *a, **kw: ["pq"]
    )
    # 显式和预测都返回 id=99
    def fake_search_with_tier(query, top_k, **kw):
        return ([{"id": 99, "content": "shared"}], [])
    monkeypatch.setattr(predictor, "search_with_tier", fake_search_with_tier)
    high, medium = predictor.search_predictive("test", "profile")
    high_ids = [c["id"] for c in high]
    assert high_ids.count(99) == 1  # dedup


# ── 6. 失败降级(2 个) ───────────────────────────────────────────
def test_search_predictive_handles_predictor_timeout(monkeypatch):
    """predictor timeout → 兜底到显式(不抛)。"""
    from impl import predictor
    monkeypatch.setattr(
        predictor, "generate_predictive_queries", lambda *a, **kw: []
    )  # timeout → [] 路径
    monkeypatch.setattr(
        predictor, "search_with_tier",
        lambda query, top_k, **kw: ([{"id": 1}], []),
    )
    high, medium = predictor.search_predictive("test", "profile")
    assert len(high) == 1


def test_search_predictive_handles_predictor_catastrophic_failure(monkeypatch):
    """search_predictive 内任何意外异常 → 兜底返回 ([], []),不抛。"""
    from impl import predictor
    # 让 search_with_tier 在第一次调用就抛
    def boom(*a, **kw):
        raise RuntimeError("simulated catastrophic failure")
    monkeypatch.setattr(predictor, "search_with_tier", boom)
    monkeypatch.setattr(predictor, "generate_predictive_queries", lambda *a, **kw: ["q"])
    high, medium = predictor.search_predictive("test", "profile")
    assert high == [] and medium == []


# ── 7. 桥层 e2e(2 个) ───────────────────────────────────────────
def test_read_user_profile_returns_concatenated_content():
    """read_user_profile() 读 user_profile.md + user_profile_auto.md,拼接返回。"""
    from impl.predictor import read_user_profile
    profile = read_user_profile()
    # 至少包含 user_profile.md 的开头
    assert "user_profile.md" in profile or "Oliver" in profile


def test_predictor_metrics_track_latency_and_timeouts(monkeypatch):
    """多次调用后,指标计数正确(成功率、超时数)。"""
    from impl import predictor
    fake_resp_ok = MagicMock()
    fake_resp_ok.text = json.dumps({
        "message": {"content": "Q1\nQ2\nQ3"}, "done": True,
    })
    fake_resp_ok.raise_for_status = MagicMock()
    call_count = [0]
    def mock_post(*a, **kw):
        call_count[0] += 1
        if call_count[0] == 3:
            raise requests.Timeout("simulated once")
        return fake_resp_ok
    monkeypatch.setattr("impl.predictor.requests.post", mock_post)
    # 3 次调用,第 3 次 timeout
    for _ in range(3):
        predictor.generate_predictive_queries(user_profile="", user_query="q")
    metrics = predictor.get_predictor_metrics()
    assert metrics["latency_count"] == 3  # 3 次都记录了 latency
    assert metrics["timeout_count"] == 1  # 1 次 timeout
    assert metrics["empty_count"] == 0  # 2 次成功,0 empty
