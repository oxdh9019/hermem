"""Hermem V6 Sprint 3 - 反射 API 单元测试(8 个)。"""

import json
from unittest.mock import patch, MagicMock

import pytest


# ── 1. 边界(3 个) ─────────────────────────────────────────────────
def test_reflect_no_sources_returns_message(monkeypatch):
    """无召回时返回提示。"""
    from impl import reflect
    # 强制 search_with_tier 返回空
    monkeypatch.setattr(reflect, "search_with_tier", lambda **kw: ([], []))
    result = reflect.hermem_reflect("no results query", top_k=3, write_l4=False)
    assert result["answer"] == "没有找到相关历史记忆。"
    assert result["sources"] == []
    assert result["l4_written"] is False


def test_reflect_basic_flow_with_mocked_llm(monkeypatch):
    """mock search_with_tier + LLM → 综合答案。"""
    from impl import reflect
    # mock 召回
    monkeypatch.setattr(
        reflect, "search_with_tier",
        lambda **kw: ([{"id": 1, "content": "V6 sprint 2 已完成", "rrf_score": 0.05}], []),
    )
    # mock LLM 综合
    fake_resp = MagicMock()
    fake_resp.text = json.dumps({
        "message": {"content": "基于 [1] 记忆,V6 sprint 2 已完成。"},
        "done": True,
    })
    fake_resp.raise_for_status = MagicMock()
    with patch("impl.predictor.requests.post", return_value=fake_resp):
        result = reflect.hermem_reflect("V6 进度", top_k=3, write_l4=False)
    assert "[1]" in result["answer"]
    assert len(result["sources"]) == 1


def test_reflect_llm_timeout_returns_error(monkeypatch):
    """LLM 超时 → 返回错误信息,不写 L4。"""
    from impl import reflect
    import requests
    monkeypatch.setattr(
        reflect, "search_with_tier",
        lambda **kw: ([{"id": 1, "content": "x"}], []),
    )
    with patch("impl.predictor.requests.post", side_effect=requests.Timeout("simulated")):
        result = reflect.hermem_reflect("q", top_k=3, write_l4=True, session_id="s1")
    assert "LLM 错误" in result["answer"]
    assert result["l4_written"] is False
    assert result["l4_id"] is None


# ── 2. write_l4 行为(3 个) ─────────────────────────────────────
def test_reflect_write_l4_disabled_by_default(monkeypatch):
    """write_l4=False 时不调 L4 write。"""
    from impl import reflect
    monkeypatch.setattr(
        reflect, "search_with_tier",
        lambda **kw: ([{"id": 1, "content": "x"}], []),
    )
    fake_resp = MagicMock()
    fake_resp.text = json.dumps({"message": {"content": "answer"}, "done": True})
    fake_resp.raise_for_status = MagicMock()
    write_l4_called = []
    def fake_write(text, sid):
        write_l4_called.append((text, sid))
        return 42
    monkeypatch.setattr(reflect, "call_predictor_llm", lambda *a, **kw: "answer")
    with patch("v5.5.impl.l4_reflection.write_reflection_immediate" if False else "builtins.__import__"):
        # 写 L4 不应该被调
        result = reflect.hermem_reflect("q", top_k=3, write_l4=False, session_id="s1")
    assert result["l4_written"] is False
    assert result["l4_id"] is None
    assert write_l4_called == []  # 写函数未被调


def test_reflect_write_l4_success(monkeypatch):
    """write_l4=True + 模拟 v5.5 import 成功 → l4_written=True。"""
    from impl import reflect
    monkeypatch.setattr(
        reflect, "search_with_tier",
        lambda **kw: ([{"id": 1, "content": "x"}], []),
    )
    # 模拟 v5.5 l4_reflection 模块存在且函数可用
    import sys as _sys
    fake_module = MagicMock()
    fake_module.write_reflection_immediate = lambda text, sid: 42
    _sys.modules["l4_reflection"] = fake_module

    # mock LLM 综合 + L4 综合
    llm_responses = ["main answer", "l4 元记忆"]
    with patch("impl.predictor.call_predictor_llm", side_effect=llm_responses):
        result = reflect.hermem_reflect("q", top_k=3, write_l4=True, session_id="s1")
    assert result["l4_written"] is True
    assert result["l4_id"] == 42
    assert result["l4_text"] == "l4 元记忆"
    assert "main answer" in result["answer"]


def test_reflect_write_l4_synthesis_failure(monkeypatch):
    """L4 综合失败 → l4_written=False。"""
    from impl import reflect
    monkeypatch.setattr(
        reflect, "search_with_tier",
        lambda **kw: ([{"id": 1, "content": "x"}], []),
    )
    import sys as _sys
    fake_module = MagicMock()
    fake_module.write_reflection_immediate = lambda text, sid: 42
    _sys.modules["l4_reflection"] = fake_module

    # main LLM 成功,L4 LLM 返回空字符串
    with patch("impl.predictor.call_predictor_llm", side_effect=["answer", ""]):
        result = reflect.hermem_reflect("q", top_k=3, write_l4=True, session_id="s1")
    assert result["l4_written"] is False
    assert result["l4_id"] is None
    assert result["l4_text"] is None


# ── 3. 召回 cap + 引用(2 个) ────────────────────────────────────
def test_reflect_sources_capped_at_top_k(monkeypatch):
    """召回 > top_k 时只取 top_k 进 context。"""
    from impl import reflect
    from impl import predictor as _predictor
    # mock 召回:返回 10 个 chunk
    fake_chunks = [{"id": i, "content": f"chunk {i}"} for i in range(10)]
    monkeypatch.setattr(
        reflect, "search_with_tier",
        lambda **kw: (fake_chunks[:3], fake_chunks[3:]),
    )
    captured = {}
    def fake_llm(prompt, timeout=3.0):
        captured["prompt"] = prompt
        return "ok"
    monkeypatch.setattr(_predictor, "call_predictor_llm", fake_llm)
    result = reflect.hermem_reflect("q", top_k=2, write_l4=False)
    # context 应只含 top_k=2 个 chunks(chunk 0 + chunk 1)
    assert "chunk 0" in captured["prompt"]
    assert "chunk 1" in captured["prompt"]
    # chunk 2+ 不在 context
    assert "chunk 2," not in captured["prompt"] and "chunk 2 " not in captured["prompt"]


def test_reflect_answer_includes_chunk_id_citation(monkeypatch):
    """答案含 [chunk_id] 引用(可追踪性)。"""
    from impl import reflect
    monkeypatch.setattr(
        reflect, "search_with_tier",
        lambda **kw: ([
            {"id": 1131, "content": "V6 sprint 2 完成"},
            {"id": 2299, "content": "Sprint 1 全部完成"},
        ], []),
    )
    with patch("impl.predictor.call_predictor_llm", return_value="V6 整体进度基于 [1131] [2299]"):
        result = reflect.hermem_reflect("V6 整体进度", top_k=3, write_l4=False)
    assert "[1131]" in result["answer"]
    assert "[2299]" in result["answer"]
