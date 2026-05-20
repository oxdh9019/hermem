"""
tests/unit/test_intent_classifier.py
=====================================
单元测试：intent_classifier.py 核心逻辑

覆盖：
- _match_triggers()  — 所有触发词完整覆盖
- IntentClassifier._parse()  — LLM 输出解析
- IntentClassifier.classify()  — Layer1 hit/Layer2 fallback 行为
"""

import sys, os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "phase3"))

import pytest
from unittest.mock import patch
from impl.intent_classifier import (
    _match_triggers,
    IntentClassifier,
    INTENT_TRIGGERS,
    INTENT_DESCRIPTIONS,
)


# ─────────────────────────────────────────────────────────────────
# _match_triggers() — 13 意图 × 关键触发词
# ─────────────────────────────────────────────────────────────────

class TestMatchTriggers_learn:
    @pytest.mark.parametrize("msg", [
        "什么是薛定谔的猫",
        "为什么天空是蓝色的",
        "怎么才能学会 Python",
        "帮我理解量子纠缠",
        "解释一下相对论",
        "科普一下区块链",
        "详细说明一下这个原理",
        "这是怎么回事",
    ])
    def test_learn_triggers(self, msg):
        assert _match_triggers(msg) == "learn"


class TestMatchTriggers_execute:
    @pytest.mark.parametrize("msg", [
        "帮我生成一段 Python 代码",
        "跑一下这个测试",
        "创建一个文件夹",
        "帮我做一个网页",
        "执行这条命令",
        "写一个爬虫脚本",
    ])
    def test_execute_triggers(self, msg):
        assert _match_triggers(msg) == "execute"


class TestMatchTriggers_correct:
    @pytest.mark.parametrize("msg", [
        "不对，我不是这个意思",
        "错了，应该是这样",
        "不是这样，纠正一下",
        "你理解错了，重新来",
    ])
    def test_correct_triggers(self, msg):
        assert _match_triggers(msg) == "correct"


class TestMatchTriggers_close:
    @pytest.mark.parametrize("msg", [
        "关闭这个流程",
        "好了，就到这里吧",
        "结束吧，可以了",
        "就这样，够了",
    ])
    def test_close_triggers(self, msg):
        assert _match_triggers(msg) == "close"


class TestMatchTriggers_feedback:
    @pytest.mark.parametrize("msg", [
        "太啰嗦了，精简一点",
        "不够具体，再详细点",
        "太简单了，再深入一些",
        "不满意，重做",
    ])
    def test_feedback_triggers(self, msg):
        assert _match_triggers(msg) == "feedback"


class TestMatchTriggers_confirm:
    @pytest.mark.parametrize("msg", [
        "你确定这是对的吗",
        "真的假的",
        "确认一下这个结论",
        "真的吗？",
    ])
    def test_confirm_triggers(self, msg):
        assert _match_triggers(msg) == "confirm"


class TestMatchTriggers_suggest:
    @pytest.mark.parametrize("msg", [
        "有什么改进建议吗",
        "给我推荐一个方案",
        "优化建议是什么",
    ])
    def test_suggest_triggers(self, msg):
        assert _match_triggers(msg) == "suggest"


class TestMatchTriggers_remember:
    @pytest.mark.parametrize("msg", [
        "你还记得我们之前讨论的吗",
        "记得上次说过的方案吗",
        "之前我们提过这个",
    ])
    def test_remember_triggers(self, msg):
        assert _match_triggers(msg) == "remember"


class TestMatchTriggers_modify:
    @pytest.mark.parametrize("msg", [
        "把这个改成中文版本",
        "语气改一下",
        "调整一下格式",
        "换一种表达方式",
    ])
    def test_modify_triggers(self, msg):
        assert _match_triggers(msg) == "modify"


class TestMatchTriggers_stop:
    @pytest.mark.parametrize("msg", [
        "算了，不用了",
        "停，取消吧",
        "取消操作",
    ])
    def test_stop_triggers(self, msg):
        assert _match_triggers(msg) == "stop"


class TestMatchTriggers_question:
    @pytest.mark.parametrize("msg", [
        "露娜是谁",
        "这个文件在哪来的",
        "你有多少个模型",
        "项目里有几个文件",
    ])
    def test_question_triggers(self, msg):
        assert _match_triggers(msg) == "question"


class TestMatchTriggers_consult:
    @pytest.mark.parametrize("msg", [
        "你对这个方案怎么看",
        "帮我分析一下这个问题",
        "你认为哪个更好",
        "你的意见是什么",
    ])
    def test_consult_triggers(self, msg):
        assert _match_triggers(msg) == "consult"


class TestMatchTriggers_evaluate:
    @pytest.mark.parametrize("msg", [
        "这两个方案哪个更好",
        "评估一下这个策略",
        "哪个选择更优",
        "比较一下 A 和 B",
    ])
    def test_evaluate_triggers(self, msg):
        assert _match_triggers(msg) == "evaluate"


class TestMatchTriggers_noMatch:
    """无触发词 → 返回 None"""
    @pytest.mark.parametrize("msg", [
        "好吧",
        "随便",
        "OK",
        "可以",
        "好的",
        "嗯",
    ])
    def test_no_match_returns_none(self, msg):
        assert _match_triggers(msg) is None


class TestMatchTriggers_caseInsensitive:
    """触发词匹配不区分大小写"""
    def test_uppercase(self):
        assert _match_triggers("什么是量子纠缠") == "learn"
    def test_mixed_case(self):
        assert _match_triggers("什么是 PYTHON") == "learn"


# ─────────────────────────────────────────────────────────────────
# IntentClassifier._parse() — LLM 输出解析
# ─────────────────────────────────────────────────────────────────

class TestIntentClassifier_parse:
    """解析各种格式的 LLM 输出"""

    def test_exact_format(self):
        c = IntentClassifier()
        assert c._parse("intent: learn") == "learn"

    def test_extra_whitespace(self):
        c = IntentClassifier()
        assert c._parse("intent:   execute  ") == "execute"

    def test_lowercase_intent(self):
        c = IntentClassifier()
        assert c._parse("intent: CORRECT") == "correct"

    def test_uppercase_intent(self):
        c = IntentClassifier()
        assert c._parse("intent: LEARN") == "learn"

    def test_no_intent_prefix_but_valid_key(self):
        c = IntentClassifier()
        assert c._parse("learn") == "learn"
        assert c._parse("execute") == "execute"

    def test_unknown_intent_returns_other(self):
        c = IntentClassifier()
        assert c._parse("intent: nonsense") == "other"

    def test_empty_returns_other(self):
        c = IntentClassifier()
        assert c._parse("") == "other"

    def test_only_intent_label_in_text(self):
        c = IntentClassifier()
        assert c._parse("我认为这是 evaluate 类型") == "evaluate"


# ─────────────────────────────────────────────────────────────────
# IntentClassifier.classify() — Layer1/Layer2 行为验证
# ─────────────────────────────────────────────────────────────────

class TestIntentClassifier_classify:
    """Layer1 命中 → 不调用 LLM；Layer1 未命中 → 调用 LLM"""

    def test_layer1_hit_skips_llm(self):
        """触发词命中 → 直接返回，不调用 llm_generate"""
        c = IntentClassifier()
        with patch("impl.intent_classifier.llm_generate") as mock_llm:
            result = c.classify("帮我详细说明一下量子纠缠")
            assert result == "learn"
            mock_llm.assert_not_called()

    def test_layer1_hit_all_intents(self):
        """所有 13 种意图的触发词都命中且不调用 LLM"""
        c = IntentClassifier()
        # 用 list 而非 dict，确保无歧义用例
        sample_messages = [
            ("learn", "什么是薛定谔的猫"),
            ("execute", "帮我生成一段代码"),
            ("correct", "不对，错了"),
            ("close", "好了，关闭吧"),
            ("feedback", "太啰嗦了，精简"),
            ("confirm", "你确定吗"),
            ("suggest", "有什么建议"),
            ("remember", "还记得吗"),
            ("modify", "把这个改成中文版本"),
            ("stop", "算了，不用了"),
            ("question", "这是谁"),
            ("consult", "你怎么看"),
            ("evaluate", "哪个更好"),
        ]
        for intent, msg in sample_messages:
            with patch("impl.intent_classifier.llm_generate") as mock_llm:
                result = c.classify(msg)
                assert result == intent, f"{intent}: expected {intent}, got {result}"
                mock_llm.assert_not_called()

    def test_layer1_miss_calls_llm(self):
        """触发词未命中 → 调用 LLM"""
        c = IntentClassifier()
        with patch.object(c, "_llm_classify", return_value="other") as mock_llm:
            result = c.classify("随便")
            assert result == "other"
            mock_llm.assert_called_once()

    def test_empty_message_returns_other(self):
        c = IntentClassifier()
        with patch("impl.intent_classifier.llm_generate") as mock_llm:
            assert c.classify("") == "other"
            assert c.classify("   ") == "other"
            mock_llm.assert_not_called()

    def test_classify_with_confirmation(self):
        c = IntentClassifier()
        intent, needs_conf = c.classify_with_confirmation("什么是量子纠缠")
        assert intent == "learn"
        assert needs_conf is False  # learn != "other"

        # 无触发词走 LLM，需要 mock _llm_classify 避免真实调用
        with patch.object(c, "_llm_classify", return_value="other"):
            intent2, needs_conf2 = c.classify_with_confirmation("好")
            assert intent2 == "other"
            assert needs_conf2 is True  # other → 需要确认


# ─────────────────────────────────────────────────────────────────
# INTENT_DESCRIPTIONS 完整性
# ─────────────────────────────────────────────────────────────────

class TestIntentCoverage:
    """确保 13 种意图都有触发词和描述"""

    def test_all_13_intents_have_triggers(self):
        for intent in INTENT_DESCRIPTIONS:
            assert intent in INTENT_TRIGGERS, f"Intent '{intent}' missing triggers"
            assert len(INTENT_TRIGGERS[intent]) > 0, f"Intent '{intent}' has no trigger words"

    def test_all_trigger_words_point_to_valid_intents(self):
        for intent, triggers in INTENT_TRIGGERS.items():
            assert intent in INTENT_DESCRIPTIONS, f"Trigger list '{intent}' has no matching description"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
