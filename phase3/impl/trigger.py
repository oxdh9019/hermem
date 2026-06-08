"""Hermem V6 Sprint 1 — 按需触发决策(4 信号)。

V5 是固定频率触发(每 N 回合),V6 改为按需触发:
1. intent_classifier 置信度低(< 0.7)
2. anchor 5 词命中
3. Temporal 关键词命中(由 temporal_parser 决定;任务 1.5)
4. 中置信累积(同一 chunk 3 轮中置信未注入)

降级:固定频率保留作为最后兜底。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

# 决策 3: anchor 5 词固定表
ANCHOR_KEYWORDS: tuple[str, ...] = (
    "上次",  # 显式追问历史
    "之前那个",  # 模糊指代历史
    "你还记得",  # 显式检查 AI 记忆
    "接着说",  # 续接前文
    "之前提到",  # 显式指代历史
)

# 决策点(intent_low 触发阈值)
INTENT_CONFIDENCE_THRESHOLD: float = 0.7

# 中置信累积触发阈值(连续 3 轮累积同一 chunk)
MEDIUM_ACCUMULATED_TURNS: int = 3


def has_anchor_keyword(message: str) -> bool:
    """检查用户消息是否含 5 词 anchor 之一。"""
    if not message:
        return False
    return any(kw in message for kw in ANCHOR_KEYWORDS)


def has_temporal_keyword(message: str) -> bool:
    """检查用户消息是否含时间词(由 temporal_parser 决定,任务 1.5)。

    这里只做粗粒度前缀判断,实际区间计算在 temporal_parser.parse_relative_time。
    """
    if not message:
        return False
    # 前缀/包含:上周/上个月/昨天/前天/N天前/YYYY-MM/Q1 2026
    TEMPORAL_HINTS = (
        "上周",
        "上个月",
        "上月",
        "昨天",
        "前天",
        "今天",
        "明天",
        "今年",
        "去年",
        "上一年",
    )
    if any(t in message for t in TEMPORAL_HINTS):
        return True
    # YYYY-MM 数字格式
    import re

    if re.search(r"\d{4}-\d{1,2}", message):
        return True
    # Q1 2026 / Q4 2025 季度格式
    if re.search(r"[Qq][1-4]\s*\d{4}", message):
        return True
    # N 天前 / N 周前 / N 个月前
    if re.search(r"\d+\s*(天|周|个月|月)前", message):
        return True
    return False


def should_trigger(
    message: str,
    intent_confidence: float,
    medium_tracker_turns: dict[str, int],
    turn_count: int,
    frequency: int = 3,
) -> tuple[bool, str | None]:
    """V6 Sprint 1 任务 1.2:4 信号综合判断是否触发主动检索。

    Args:
        message: 当前用户消息
        intent_confidence: 上一轮 intent_classifier 返回的 confidence
        medium_tracker_turns: {chunk_id: 累积轮数},由调用方维护
        turn_count: 当前总回合数(用于频率兜底)
        frequency: 固定频率兜底(默认每 3 回合)

    Returns:
        (should_trigger, source):
        - source ∈ {'intent_low' / 'anchor_keyword' / 'temporal' / 'medium_accumulated' / 'frequency_fallback'}
        - source=None 表示不触发

    优先级:medium_accumulated > anchor_keyword > temporal > intent_low > frequency_fallback
    (medium_accumulated 优先 — 因为它已经累积了 3 轮,最确定用户真的在问)
    """
    # 信号 4:中置信累积(优先)
    for cid, turns in medium_tracker_turns.items():
        if turns >= MEDIUM_ACCUMULATED_TURNS:
            return True, "medium_accumulated"

    # 信号 2:anchor 5 词
    if has_anchor_keyword(message):
        return True, "anchor_keyword"

    # 信号 3:Temporal 时间词
    if has_temporal_keyword(message):
        return True, "temporal"

    # 信号 1:intent 置信度低
    if intent_confidence < INTENT_CONFIDENCE_THRESHOLD:
        return True, "intent_low"

    # 兜底:固定频率
    if frequency > 0 and turn_count > 0 and turn_count % frequency == 0:
        return True, "frequency_fallback"

    return False, None
