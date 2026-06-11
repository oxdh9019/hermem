"""Sprint 4 任务 4.4 修根因测试 — 验证 A(normalize_query 内置) + B(零向量检测)。"""

from unittest.mock import MagicMock, patch

import pytest


# ── A: normalize_query 内置到 search_with_tier ──────────────────

def test_normalize_query_unit():
    """normalize_query 单元测试(去问号 + 问句尾词)。"""
    from impl.vector_search import normalize_query

    # 问号去除
    assert normalize_query('ds2api 工具怎么用？') == 'ds2api 工具'
    assert normalize_query('Hermem V5 核心方案是什么？') == 'Hermem V5 核心方案'
    # 不该破坏正常 query
    assert normalize_query('ds2api') == 'ds2api'
    # 空字符串
    assert normalize_query('') == ''
    assert normalize_query(None) is None


def test_search_with_tier_auto_normalizes():
    """search_with_tier 自动 normalize(修根因 A):带/不带问句词应返回相同结果。"""
    from impl.vector_search import search_with_tier

    h1, m1 = search_with_tier(query='连环画三视图生成最佳实践是什么？', top_k=3)
    h2, m2 = search_with_tier(query='连环画三视图生成最佳实践', top_k=3)

    ids1 = [c.get('id') or c.get('chunk_id') for c in h1 + m1]
    ids2 = [c.get('id') or c.get('chunk_id') for c in h2 + m2]
    assert ids1 == ids2, f"normalize 不一致: {ids1} vs {ids2}"


def test_search_with_tier_preserves_query_embedding():
    """search_with_tier 传 query_embedding 时不 normalize(query 已 None)。"""
    import numpy as np
    from impl.vector_search import search_with_tier

    fake_emb = np.zeros(1024, dtype="float32")  # 0 向量,但不走 norm 检测
    # query_embedding 直接传,query=None,应工作
    h, m = search_with_tier(query=None, query_embedding=fake_emb, top_k=3)
    # 不崩,返回空(high/medium 都是空,因 vec 全 0)
    assert isinstance(h, list)
    assert isinstance(m, list)


# ── B: embedding 零向量检测 + retry + 异常抛出 ──────────────────

def test_embedding_cached_normal_text():
    """正常文本 → 返回 1024-d 非零向量。"""
    from impl.embedding import get_embedding_cached

    emb, src = get_embedding_cached('test embedding normal text for cache')
    assert len(emb) == 1024
    assert any(x != 0 for x in emb)


def test_embedding_retry_on_zero_vector():
    """第一次返回 0 向量,第二次正常 → retry 成功。"""
    from impl.embedding import _call_ollama_with_retry

    call_count = [0]
    def fake_embeddings(**kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return {'embedding': [0.0] * 1024}
        return {'embedding': [0.01 * (i + 1) for i in range(1024)]}

    client = MagicMock()
    client.embeddings = fake_embeddings

    emb = _call_ollama_with_retry(client, 'test', max_retries=1)
    assert call_count[0] == 2
    assert any(x != 0 for x in emb)


def test_embedding_raise_on_persistent_zero():
    """两次都 0 → 抛 EmbeddingZeroNormError。"""
    from impl.embedding import _call_ollama_with_retry, EmbeddingZeroNormError

    call_count = [0]
    def always_zero(**kw):
        call_count[0] += 1
        return {'embedding': [0.0] * 1024}

    client = MagicMock()
    client.embeddings = always_zero

    with pytest.raises(EmbeddingZeroNormError):
        _call_ollama_with_retry(client, 'test', max_retries=1)
    assert call_count[0] == 2


def test_embedding_raise_on_nan_vector():
    """NaN 向量 → 抛 EmbeddingZeroNormError。"""
    from impl.embedding import _call_ollama_with_retry, EmbeddingZeroNormError

    def nan_vec(**kw):
        emb = [0.1] * 1024
        emb[0] = float('nan')
        return {'embedding': emb}

    client = MagicMock()
    client.embeddings = nan_vec

    with pytest.raises(EmbeddingZeroNormError):
        _call_ollama_with_retry(client, 'test', max_retries=1)
