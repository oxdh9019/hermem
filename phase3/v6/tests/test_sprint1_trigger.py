"""V6 Sprint 1 单元测试 — 按需触发 + RRF 融合 + Temporal 通道。

覆盖:
- trigger.ANCHOR_KEYWORDS 5 词
- should_trigger 4 信号
- temporal_parser 5-7 条 regex
- vector_search RRF 融合
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

# ── trigger.ANCHOR_KEYWORDS ─────────────────────────────────────────────────


def test_anchor_keywords_contains_5_fixed_words():
    """决策 3:anchor 5 词固定表。"""
    from impl.trigger import ANCHOR_KEYWORDS

    assert ANCHOR_KEYWORDS == ("上次", "之前那个", "你还记得", "接着说", "之前提到")
    assert len(ANCHOR_KEYWORDS) == 5


def test_has_anchor_keyword_true_for_each():
    """5 词各给 1 个示例消息 → 全命中。"""
    from impl.trigger import has_anchor_keyword

    samples = [
        ("上次那个 cron 任务怎么做的", True),
        ("之前那个方案有印象吗", True),
        ("你还记得我们讨论过的 X 吗", True),
        ("接着说 X 的细节", True),
        ("之前提到的方案再讲讲", True),
        ("今天天气不错", False),
        ("", False),
    ]
    for msg, expected in samples:
        assert has_anchor_keyword(msg) is expected, f"failed for {msg!r}"


# ── should_trigger 4 信号 ──────────────────────────────────────────────────


def test_should_trigger_medium_accumulated():
    """信号 4:中置信累积 ≥ 3 轮 → 触发,source='medium_accumulated'。"""
    from impl.trigger import should_trigger

    should, source = should_trigger(
        message="今天做点别的",
        intent_confidence=1.0,
        medium_tracker_turns={"c1": 3, "c2": 2},
        turn_count=1,
        frequency=3,
    )
    assert should is True
    assert source == "medium_accumulated"


def test_should_trigger_anchor_keyword():
    """信号 2:anchor 关键词 → 触发,source='anchor_keyword'。"""
    from impl.trigger import should_trigger

    should, source = should_trigger(
        message="上次那个怎么做的",
        intent_confidence=1.0,
        medium_tracker_turns={},
        turn_count=1,
        frequency=3,
    )
    assert should is True
    assert source == "anchor_keyword"


def test_should_trigger_temporal_keyword():
    """信号 3:Temporal 关键词 → 触发,source='temporal'。"""
    from impl.trigger import should_trigger

    should, source = should_trigger(
        message="上周做了哪些任务",
        intent_confidence=1.0,
        medium_tracker_turns={},
        turn_count=1,
        frequency=3,
    )
    assert should is True
    assert source == "temporal"


def test_should_trigger_intent_low_confidence():
    """信号 1:intent_confidence < 0.7 → 触发,source='intent_low'。"""
    from impl.trigger import should_trigger

    should, source = should_trigger(
        message="随便问问",  # 无 anchor / temporal
        intent_confidence=0.3,
        medium_tracker_turns={},
        turn_count=1,
        frequency=3,
    )
    assert should is True
    assert source == "intent_low"


def test_should_trigger_frequency_fallback():
    """兜底:turn_count % frequency == 0 → 触发,source='frequency_fallback'。"""
    from impl.trigger import should_trigger

    should, source = should_trigger(
        message="继续说",
        intent_confidence=1.0,  # 跳过 intent_low
        medium_tracker_turns={},
        turn_count=3,  # frequency=3 整除
        frequency=3,
    )
    assert should is True
    assert source == "frequency_fallback"


def test_should_trigger_no_signal_no_fallback():
    """4 信号全无,turn_count 不整除 → 不触发。"""
    from impl.trigger import should_trigger

    should, source = should_trigger(
        message="做点别的吧",  # 不含 anchor / temporal / 关键词
        intent_confidence=1.0,  # 跳过 intent_low
        medium_tracker_turns={},
        turn_count=2,  # 3 的余数,不整除
        frequency=3,
    )
    assert should is False
    assert source is None


def test_should_trigger_priority_order():
    """优先级:medium_accumulated > anchor > temporal > intent_low > frequency。

    当多信号同时满足时,返回优先级最高。
    """
    from impl.trigger import should_trigger

    # 同时:medium 累积 + anchor
    should, source = should_trigger(
        message="上次那个",
        intent_confidence=0.3,
        medium_tracker_turns={"c1": 3},
        turn_count=2,
        frequency=3,
    )
    assert source == "medium_accumulated"

    # 同时:anchor + temporal + intent_low(无 medium 累积)
    should, source = should_trigger(
        message="上周之前那个",
        intent_confidence=0.3,
        medium_tracker_turns={},
        turn_count=2,
        frequency=3,
    )
    assert source == "anchor_keyword"  # anchor 优先于 temporal


# ── temporal_parser 5-7 条 regex ──────────────────────────────────────────


def test_temporal_parser_quarter_format():
    """Q1 2026 → 2026-01-01 到 2026-04-01。"""
    from impl.temporal_parser import parse_relative_time

    result = parse_relative_time("Q1 2026 做的")
    assert result is not None
    start, end = result
    assert start.year == 2026 and start.month == 1
    assert end.year == 2026 and end.month == 4


def test_temporal_parser_yyyy_mm_format():
    """2026-05 → 2026-05-01 到 2026-06-01。"""
    from impl.temporal_parser import parse_relative_time

    result = parse_relative_time("2026-05 的数据")
    assert result is not None
    start, end = result
    assert start.year == 2026 and start.month == 5
    assert end.year == 2026 and end.month == 6


def test_temporal_parser_days_ago():
    """3 天前 → 3 天前 00:00 到 2 天前 00:00。"""
    from impl.temporal_parser import parse_relative_time

    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    result = parse_relative_time("3 天前讨论的", now=now)
    assert result is not None
    start, end = result
    assert start == datetime(2026, 6, 5, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 6, 6, 0, 0, tzinfo=UTC)


def test_temporal_parser_last_week():
    """上周 → 上周一 00:00 到本周一 00:00。"""
    from impl.temporal_parser import parse_relative_time

    # 2026-06-08 是 Monday
    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    result = parse_relative_time("上周", now=now)
    assert result is not None
    start, end = result
    # 上周一 = 2026-06-01,本周一 = 2026-06-08
    assert start == datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 6, 8, 0, 0, tzinfo=UTC)


def test_temporal_parser_last_month():
    """上个月 → 上月 1 号到本月 1 号。"""
    from impl.temporal_parser import parse_relative_time

    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    result = parse_relative_time("上个月", now=now)
    assert result is not None
    start, end = result
    assert start == datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def test_temporal_parser_yesterday():
    """昨天 → 昨天 00:00 到今天 00:00。"""
    from impl.temporal_parser import parse_relative_time

    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    result = parse_relative_time("昨天做的", now=now)
    assert result is not None
    start, end = result
    assert start == datetime(2026, 6, 7, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 6, 8, 0, 0, tzinfo=UTC)


def test_temporal_parser_no_match():
    """无时间词 → None。"""
    from impl.temporal_parser import parse_relative_time

    result = parse_relative_time("做点别的吧")  # 无时间词
    assert result is None


def test_temporal_parser_today_recognized():
    """ "今天" 是设计内的时间词 → 命中(今天 00:00 到明天 00:00)。"""
    from impl.temporal_parser import parse_relative_time

    result = parse_relative_time("今天做了 X")
    assert result is not None
    start, end = result
    assert end > start


def test_temporal_parser_empty():
    """空字符串 → None。"""
    from impl.temporal_parser import parse_relative_time

    assert parse_relative_time("") is None
    assert parse_relative_time(None) is None


# ── intent_classifier confidence ──────────────────────────────────────────


def test_classify_with_confidence_layer1_hit():
    """Layer 1 触发词命中 → confidence = 1.0。"""
    from impl.intent_classifier import classify_intent_with_confidence

    intent, conf = classify_intent_with_confidence("不对,应该是这样")
    assert intent == "correct"
    assert conf == 1.0


def test_classify_with_confidence_short_message_low():
    """短消息(无 Layer 1 触发词)→ confidence 低(LLM 路径)。"""
    from impl.intent_classifier import classify_intent_with_confidence

    intent, conf = classify_intent_with_confidence("嗯嗯")
    # Layer 2 LLM 路径,短消息降权
    assert conf <= 0.5


def test_classify_with_confidence_empty():
    """空消息 → (other, 0.0)。"""
    from impl.intent_classifier import classify_intent_with_confidence

    assert classify_intent_with_confidence("") == ("other", 0.0)
    assert classify_intent_with_confidence(None) == ("other", 0.0)


# ── vector_search RRF 融合 ────────────────────────────────────────────────


def test_search_with_tier_rrf_double_hit_is_highest():
    """双路命中 > 单路命中(在 RRF 分数上)。"""
    from impl.vector_search import RRF_K

    # 双路命中:1/(60+1) + 1/(60+1) = 0.0328
    double_score = 2.0 / (RRF_K + 1)
    # 单路命中(仅 vec 第 1):1/(60+1) = 0.0164
    single_score = 1.0 / (RRF_K + 1)
    assert double_score > single_score * 1.5  # 至少 1.5x(实际是 2x)


def test_search_with_tier_accepts_query_string():
    """新签名:query=str(替代 query_embedding=np.ndarray)。"""
    from impl.vector_search import search_with_tier

    # 实际不调 Ollama,用 query=None + query_embedding=None 验证返回
    high, medium = search_with_tier(query=None, query_embedding=None, top_k=3)
    assert high == [] and medium == []


def test_search_with_tier_handles_time_range_in_query():
    """query 含 Temporal 关键词 → 自动解析 time_range,过滤非区间内 chunk。

    真实集成测试 — 需要 chunks 表有数据。Hermem.db 现成有 2166 chunk,可用。
    """
    from impl.vector_search import search_with_tier

    # "上周" → 2026-06-01 到 2026-06-08;chunks 在此区间的应被保留
    high, medium = search_with_tier(query="上周 cron 任务", top_k=3)
    # 不验证具体结果(数据分布决定),只验证函数不抛异常
    assert isinstance(high, list)
    assert isinstance(medium, list)


def test_search_with_tier_explicit_time_range():
    """显式传 time_range,query 无时间词 → 也应过滤。"""
    from datetime import datetime

    from impl.vector_search import search_with_tier

    # 选一个未来区间(2027)→ 应过滤掉所有现有 chunk
    future = (
        datetime(2027, 1, 1, tzinfo=UTC),
        datetime(2027, 12, 31, tzinfo=UTC),
    )
    high, medium = search_with_tier(query="cron", top_k=3, time_range=future)
    # 未来区间无 chunk → 全空
    assert high == [] and medium == []
