#!/usr/bin/env python3
"""
Hermem Phase 3 - Intent Classifier

两层判断架构：
  Layer 1: Fallback 触发词匹配（快速、精确、无幻觉）
  Layer 2: LLM 判断（兜底，当 Layer 1 没命中时）

Oliver 13 意图清单:
  learn     | 学习      — 想了解、掌握某个知识或概念
  execute   | 执行      — 想让 AI 完成某个任务
  correct   | 修正      — 想纠正某个错误
  close     | 结束/关闭  — 想结束当前流程
  feedback  | 反馈      — 想对结果表达意见
  confirm   | 确认      — 想确认某个判断
  suggest   | 建议      — 想听建议
  remember  | 记忆      — 想触发记忆检索
  modify    | 修改      — 想修改内容/行为
  stop      | 停止      — 想中止
  question  | 提问      — 想得到答案
  consult   | 咨询      — 想听 AI 的看法/分析
  evaluate  | 评估      — 想比较/判断选项
"""

import re
from typing import Optional

from .utils import llm_generate

# ── Intent Definitions ───────────────────────────────────────────────────────

INTENT_DESCRIPTIONS = {
    "learn": "用户想了解某个概念、原理或知识，需要解释说明",
    "execute": "用户想让AI完成一个任务，如生成、创建、运行、构建某样东西",
    "correct": "用户认为AI说错了或做错了，想要纠正",
    "close": "用户想结束当前流程，表示够了/完成了/就这样",
    "feedback": "用户对AI的输出结果表达意见，如太啰嗦、不够详细、不满意",
    "confirm": "用户想确认某个判断是否正确，要求AI核实",
    "suggest": "用户想听建议、推荐或改进意见",
    "remember": "用户想触发记忆检索，提到过去讨论过的事情",
    "modify": "用户想让AI修改内容，如改语气、改格式、调整",
    "stop": "用户想中止操作，说算了/取消/停",
    "question": "用户有一个具体问题想要答案，如什么是/为什么/如何",
    "consult": "用户想让AI提供看法、分析或建议",
    "evaluate": "用户想让AI比较选项或判断哪个更好/更优",
}


# ── Layer 1: Fallback 触发词（高精度、覆盖常见表达）───────────────────────────

INTENT_TRIGGERS = {
    "learn": [
        # 概念询问（前缀）
        "什么是",
        "为什么",
        "如何",
        "怎样",
        "怎么回事",
        "什么原因",
        "哪个是",
        "怎么才能",
        # 解释类
        "详细说明",
        "解释一下",
        "讲讲",
        "具体是什么",
        "帮我理解",
        "这是怎么回事",
        "背后原理",
        "什么意思",
        "科普一下",
        "说明一下",
        "介绍",
        "阐述",
    ],
    "execute": [
        "帮我做",
        "跑一下",
        "生成",
        "创建",
        "构建",
        "执行",
        "运行",
        "写一个",
        "写段",
        "生成一段",
        "制作",
    ],
    "correct": [
        "不对",
        "错了",
        "不是这个",
        "不是这样",
        "纠正",
        "修正",
        "重新来",
        "重新做",
    ],
    "close": [
        "关闭",
        "好了",
        "就这样",
        "结束",
        "完成",
        "可以了",
        "够了",
        "到此为止",
        "结束吧",
    ],
    "feedback": [
        "太啰嗦",
        "不够具体",
        "再详细点",
        "太简单",
        "不满意",
        "不够好",
        "太差了",
        "更简洁",
        "精简一点",
    ],
    "confirm": [
        "你确定吗",
        "真的假的",
        "确认一下",
        "核实",
        "真的",
        "确定是",
        "肯定吗",
        "是吗",
        "确定",
    ],
    "suggest": [
        "有什么建议",
        "推荐",
        "改进意见",
        "改进建议",
        "优化建议",
        "建议用",
        "怎么改",
        "哪些地方可以",
        "还能怎么",
    ],
    "remember": [
        "还记得吗",
        "记得",
        "提起过",
        "之前我们",
        "上次",
        "过去",
        "曾经",
        "以前说过",
    ],
    "modify": [
        "改成",
        "改一下",
        "调整",
        "换个方式",
        "换一种",
        "修改",
        "改动",
        "改变",
    ],
    "stop": [
        "算了",
        "不用了",
        "停",
        "取消",
        "停止",
    ],
    "question": [
        # learn 里已经包含了什么是/为什么/如何/怎样
        # 这里只留不包含"觉得"/"判断"的纯提问
        "哪来的",
        "是谁",
        "多少",
        "几个",
    ],
    "consult": [
        "你怎么看",
        "你的看法",
        "帮我分析",
        "你感觉",
        "你认为",
        "你的意见",
        "你觉得哪个",
        "对这个",
    ],
    "evaluate": [
        "哪个更好",
        "哪个更优",
        "评估一下",
        "判断",
        "哪个方案",
        "哪个选择",
        "比较",
    ],
}


# ── Layer 1: 触发词匹配 ───────────────────────────────────────────────────────


def _match_triggers(message: str) -> str | None:
    """
    Layer 1: 遍历所有触发词，找最早命中的意图。
    返回命中的意图标签，或 None（未命中）。
    """
    msg_lower = message.lower()
    for intent, triggers in INTENT_TRIGGERS.items():
        for t in triggers:
            if t in msg_lower:
                return intent
    return None


def _estimate_confidence(intent: str, msg_lower: str) -> float:
    """V6 Sprint 1 任务 1.1:启发式 confidence(0-1)。

    不依赖 LLM logit(OpenAI/Claude API 多数不暴露)。基于:
    - 意图标签是否在 13 类定义中(基础 0.5)
    - 该意图的触发词在消息中命中数(每命中 1 个 +0.15,封顶 0.95)
    - 短消息(< 5 字符)降权到 0.3
    - "other" 标签固定 0.2(低置信,触发 _v6_should_trigger)
    """
    if not intent or intent == "other":
        return 0.2

    if intent not in INTENT_DESCRIPTIONS:
        return 0.3

    # 基础分
    confidence = 0.5

    # 触发词命中数加分
    triggers = INTENT_TRIGGERS.get(intent, [])
    hit_count = sum(1 for t in triggers if t in msg_lower)
    confidence += min(0.45, hit_count * 0.15)

    # 短消息降权
    if len(msg_lower) < 5:
        confidence = min(confidence, 0.3)

    return round(min(0.95, confidence), 2)


# ── Layer 2: LLM 判断（仅当 Layer 1 未命中时）────────────────────────────────

CLASSIFY_PROMPT = """Oliver 正在使用 AI 助手。请判断他当前消息的意图。

【13 种意图定义】
{intent_list}

【判断规则】
- 输出**只**能是上述 13 种意图之一，或 "other"
- 如果消息无法归类到 13 种意图，输出 "other"
- 不要推理过程，只要意图标签

【消息】
{message}

【输出格式】
intent: <意图标签>"""


INTENT_LIST_FOR_PROMPT = "\n".join(f"  {k}: {v}" for k, v in INTENT_DESCRIPTIONS.items())


# ── Classifier ────────────────────────────────────────────────────────────────


class IntentClassifier:
    """
    两层判断：Layer 1 触发词匹配（快速），Layer 2 LLM（兜底）。

    使用方式:
        classifier = IntentClassifier()
        intent = classifier.classify("帮我详细说明一下量子纠缠")
        # → "learn"

        if intent == "other":
            # 询问用户确认
            pass
    """

    def __init__(self, model: str = "qwen3.5:4b-no-think"):
        self.model = model

    def classify(self, message: str) -> str:
        if not message or not message.strip():
            return "other"

        # Layer 1: 触发词快速匹配
        matched = _match_triggers(message.strip())
        if matched:
            return matched

        # Layer 2: LLM 判断
        return self._llm_classify(message.strip())

    def classify_with_confidence(self, message: str | None) -> tuple[str, float]:
        """V6 Sprint 1 任务 1.1:返回 (intent_label, confidence)。

        Args:
            message: 用户消息

        Returns:
            (intent_label, confidence): confidence 0-1,越高越确定
            - Layer 1 触发词命中:confidence = 1.0(高确定)
            - Layer 2 LLM:基于关键词命中数 heuristic 0-1
            - 失败 fallback:"other" / 0.0

        设计依据:v2.0 SPEC 决策 — 借鉴 Memory Box 1.1 "LLM 不决策只生成",
        confidence 不由 LLM 直接返回,降级用关键词匹配启发式计算。
        """
        if not message or not message.strip():
            return "other", 0.0

        message_clean = message.strip()
        msg_lower = message_clean.lower()

        # Layer 1: 触发词快速匹配 → confidence = 1.0
        matched = _match_triggers(message_clean)
        if matched:
            return matched, 1.0

        # Layer 2: LLM 判断 + confidence 启发式
        intent = self._llm_classify(message_clean)
        confidence = _estimate_confidence(intent, msg_lower)
        return intent, confidence

    def _llm_classify(self, message: str) -> str:
        prompt = CLASSIFY_PROMPT.format(
            intent_list=INTENT_LIST_FOR_PROMPT,
            message=message,
        )
        raw = llm_generate(
            prompt,
            model=self.model,
            temperature=0.1,
            max_tokens=64,
        )
        return self._parse(raw)

    def _parse(self, raw: str) -> str:
        """从 LLM 输出中提取意图标签"""
        m = re.search(r"intent:\s*(\w+)", raw.strip(), re.IGNORECASE)
        if m:
            intent = m.group(1).lower()
            if intent in INTENT_DESCRIPTIONS or intent == "other":
                return intent
        for key in INTENT_DESCRIPTIONS:
            if key in raw.strip().lower():
                return key
        return "other"

    def classify_with_confirmation(self, message: str) -> tuple[str, bool]:
        """
        Returns: (intent, needs_confirmation)
        - needs_confirmation = True 当且仅当 intent == "other"
        """
        intent = self.classify(message)
        return intent, (intent == "other")


# ── Convenience wrapper ─────────────────────────────────────────────────────

_classifier: IntentClassifier | None = None


def classify_intent(message: str) -> str:
    """全局单例入口"""
    global _classifier
    if _classifier is None:
        _classifier = IntentClassifier()
    return _classifier.classify(message)


def classify_intent_with_confidence(message: str | None) -> tuple[str, float]:
    """V6 Sprint 1 任务 1.1:全局单例入口,返回 (intent, confidence)。"""
    global _classifier
    if _classifier is None:
        _classifier = IntentClassifier()
    return _classifier.classify_with_confidence(message)


# ── 自测 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(__file__).rsplit("/", 2)[0])
    from impl.utils import llm_generate

    test_cases = [
        # learn
        ("learn", "帮我详细说明一下量子纠缠"),
        ("learn", "解释一下为什么天空是蓝色的"),
        ("learn", "什么是薛定谔的猫"),
        # execute
        ("execute", "帮我生成一段 Python 代码"),
        ("execute", "跑一下这个测试"),
        ("execute", "创建一个文件夹"),
        # correct
        ("correct", "不对，我不是这个意思"),
        ("correct", "错了，应该是这样"),
        ("correct", "不是这样，纠正一下"),
        # close
        ("close", "关闭这个流程"),
        ("close", "好了，就到这里吧"),
        # feedback
        ("feedback", "太啰嗦了，精简一点"),
        ("feedback", "不够具体，再详细点"),
        # confirm
        ("confirm", "你确定这是对的吗"),
        ("confirm", "真的假的"),
        # suggest
        ("suggest", "有什么改进建议吗"),
        ("suggest", "你觉得哪个方案更好"),
        # remember
        ("remember", "你还记得我们之前讨论的内容吗"),
        ("remember", "记得上次我们说过的那个方案吗"),
        # modify
        ("modify", "把这个改成更简洁的版本"),
        ("modify", "语气改一下，更正式一点"),
        # stop
        ("stop", "算了，不用了"),
        ("stop", "停，取消吧"),
        # question
        ("question", "什么是露娜"),
        ("question", "为什么地球是圆的"),
        # consult
        ("consult", "你对这个方案你怎么看"),
        ("consult", "帮我分析一下这个问题"),
        # evaluate
        ("evaluate", "这两个方案哪个更好"),
        ("evaluate", "评估一下这个策略的优劣"),
        # other（无触发词）
        ("other", "好吧"),
        ("other", "随便"),
        ("other", "OK"),
    ]

    classifier = IntentClassifier()
    print("=== Intent Classifier 自测 ===\n")
    for expected, msg in test_cases:
        intent = classifier.classify(msg)
        ok = "✅" if intent == expected else f"❌ expected={expected}"
        marker = " ⬅️ other（需确认）" if intent == "other" else ""
        print(f"{ok} [{intent:10s}] {msg}{marker}")
    print("\n✓ 自测完成")
    print("\n✓ 自测完成")
