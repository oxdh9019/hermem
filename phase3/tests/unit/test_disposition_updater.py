"""
tests/unit/test_disposition_updater.py
=====================================
单元测试：disposition_updater.py 核心纯函数

覆盖：
- extract_keywords()  中英文分词、停用词过滤、bigram
- compute_disposition_weight()  B6 衰减公式：f_time × f_freq
"""

import sys, os
from pathlib import Path

# 确保 impl 模块可导入
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "phase3"))

import pytest
from impl.disposition_updater import extract_keywords, compute_disposition_weight


# ─────────────────────────────────────────────────────────────────
# extract_keywords() 测试
# ─────────────────────────────────────────────────────────────────

class TestExtractKeywords_Basics:
    """基础功能"""

    def test_empty_string(self):
        assert extract_keywords("") == set()

    def test_none_input(self):
        assert extract_keywords(None) == set()

    def test_stopwords_filtered(self):
        # 常见停用词不应出现在结果中
        result = extract_keywords("我的主人翁是一个可爱的人")
        assert "的" not in result
        assert "了" not in result
        assert "是" not in result
        assert "我" not in result
        assert "人" not in result  # 单字被 len>1 过滤
        assert "一个" in result    # bigram 被保留
        assert "可爱" in result

    def test_english_stopwords_filtered(self):
        result = extract_keywords("the quick brown fox jumps over a lazy dog")
        assert "the" not in result
        assert "a" not in result
        # "over" 不在停用词表，应被保留
        assert "over" in result
        assert "fox" in result
        assert "quick" in result
        assert "brown" in result


class TestExtractKeywords_Chinese:
    """中文 bigram + unigram"""

    def test_chinese_unigram(self):
        result = extract_keywords("苹果")
        assert "苹果" in result
        # 单字被 len>1 过滤，不保留
        assert "果" not in result
        assert "苹" not in result

    def test_chinese_bigram(self):
        result = extract_keywords("机器学习")
        assert "机器" in result
        assert "学习" in result
        assert "机" not in result  # 单字被 len>1 过滤
        assert "器" not in result  # 单字被 len>1 过滤

    def test_chinese_no_stopwords(self):
        result = extract_keywords("我的代码有问题")
        assert "的" not in result
        assert "我" not in result
        assert "有" not in result
        assert "问题" in result
        assert "代码" in result

    def test_mixed_chinese_english(self):
        result = extract_keywords("Python 是一种编程语言")
        assert "python" in result
        assert "编程" in result
        assert "语言" in result
        # 停用词过滤
        assert "是" not in result
        assert "一" not in result  # 单字被过滤
        assert "一种" in result    # bigram 被保留


class TestExtractKeywords_KeywordOverlap:
    """为 match_disposition 的关键词交集逻辑提供保障"""

    def test_overlap_between_related_terms(self):
        """语义相近的词应该有交集"""
        kw1 = extract_keywords("annotation 触发条件 错误类型")
        kw2 = extract_keywords("触发条件 错误类型 误差检测")
        overlap = kw1 & kw2
        # "触发条件" 和 "错误类型" 应该在交集里
        assert len(overlap) >= 2

    def test_no_overlap_between_unrelated_terms(self):
        """无关文本交集应为空或极少"""
        kw1 = extract_keywords("Python 编程 代码")
        kw2 = extract_keywords("音乐 钢琴 演奏")
        overlap = kw1 & kw2
        assert len(overlap) == 0

    def test_chinese_bigram_overlap(self):
        """中文 bigram 产生有效交集"""
        kw1 = extract_keywords("机器学习模型")
        kw2 = extract_keywords("深度学习模型")
        overlap = kw1 & kw2
        # "学习" + "模型" 应该有交集
        assert "学习" in overlap
        assert "模型" in overlap


# ─────────────────────────────────────────────────────────────────
# compute_disposition_weight() 测试 — B6 衰减公式
# ─────────────────────────────────────────────────────────────────

class TestDispositionWeight_fFreq:
    """频次增强因子 f_freq 边界测试"""

    def test_error_count_zero_penalizes(self):
        """error_count=0 → f_freq=0.5，抑制权重"""
        w = compute_disposition_weight(last_error_at=None, error_count=0)
        assert w == 0.5

    def test_error_count_one_neutral(self):
        """error_count=1 → f_freq=1.0，中性"""
        w = compute_disposition_weight(last_error_at=None, error_count=1)
        assert w == 1.0

    def test_error_count_two_gradual_increase(self):
        """error_count=2 → f_freq=1.2（1.0 + 0.2）"""
        w = compute_disposition_weight(last_error_at=None, error_count=2)
        assert w == pytest.approx(1.2)

    def test_error_count_five_capped(self, half_life_days=7.0, max_factor=2.0):
        """error_count 足够大时 f_freq 上限为 max_factor"""
        # 1 + (20-1)*0.2 = 4.8 > 2.0，上限 cap
        w = compute_disposition_weight(last_error_at=None, error_count=20,
                                       max_factor=max_factor)
        assert w == pytest.approx(2.0)

    def test_frequency_factor_only_depends_on_error_count(self):
        """f_freq 只由 error_count 决定，与时间无关"""
        recent = "2026-05-20T10:00:00"
        w1 = compute_disposition_weight(last_error_at=recent, error_count=3)
        w2 = compute_disposition_weight(last_error_at=None, error_count=3)
        # f_freq 相同(1.4)，f_time 不同，但误差足够大时可观察到差异
        assert w1 != w2  # 至少有差异（如果 last_error_at 是今天）


class TestDispositionWeight_fTime:
    """时间衰减因子 f_time 边界测试"""

    def test_no_last_error_time_fresh(self):
        """last_error_at=None → f_time=1.0（最新鲜）"""
        w = compute_disposition_weight(last_error_at=None, error_count=1)
        assert w == pytest.approx(1.0)

    def test_half_life_decay(self):
        """半衰期后 f_time = 0.5"""
        import datetime
        half_life_days = 7.0
        exactly_half_life_ago = (
            datetime.datetime.now() - datetime.timedelta(days=half_life_days)
        ).isoformat()
        w = compute_disposition_weight(
            last_error_at=exactly_half_life_ago,
            error_count=1,  # f_freq=1.0
            half_life_days=half_life_days,
        )
        assert w == pytest.approx(0.5, abs=0.02)  # 允许 2% 误差

    def test_double_half_life_decay(self):
        """2倍半衰期后 f_time ≈ 0.25"""
        import datetime
        half_life_days = 7.0
        double_half_life_ago = (
            datetime.datetime.now() - datetime.timedelta(days=half_life_days * 2)
        ).isoformat()
        w = compute_disposition_weight(
            last_error_at=double_half_life_ago,
            error_count=1,
            half_life_days=half_life_days,
        )
        assert w == pytest.approx(0.25, abs=0.02)

    def test_invalid_last_error_at_fallback(self):
        """无效的 last_error_at 格式 → f_time=1.0"""
        w = compute_disposition_weight(last_error_at="not-a-date", error_count=1)
        assert w == pytest.approx(1.0)


class TestDispositionWeight_Combined:
    """f_time × f_freq 联合计算"""

    def test_recent_high_frequency_wins(self):
        """近期 + 高频 → 最高权重"""
        import datetime
        recent = (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()
        w = compute_disposition_weight(
            last_error_at=recent,
            error_count=5,
            half_life_days=7.0,
        )
        # f_time≈0.9, f_freq=1.8 → weight≈1.62
        assert w > 1.5

    def test_old_low_frequency_minimal(self):
        """远期 + 低频 → 最低权重"""
        import datetime
        old = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat()
        w = compute_disposition_weight(
            last_error_at=old,
            error_count=0,
            half_life_days=7.0,
        )
        # f_time≈0.03, f_freq=0.5 → weight≈0.015
        assert w < 0.1

    def test_weight_bounds(self):
        """权重在合理范围内"""
        import datetime
        now = datetime.datetime.now().isoformat()
        for ec in range(0, 30):
            w = compute_disposition_weight(
                last_error_at=now,
                error_count=ec,
                half_life_days=7.0,
                max_factor=2.0,
            )
            # 最小约 0.5*0.03=0.015，最大不超过 max_factor=2.0
            assert 0.0 < w <= 2.0, f"error_count={ec}, weight={w} out of range"

    def test_weight_increases_with_error_count(self):
        """相同时间下，error_count 越高权重越高（到 cap 前）"""
        import datetime
        same_time = (datetime.datetime.now() - datetime.timedelta(days=3)).isoformat()
        weights = {
            ec: compute_disposition_weight(last_error_at=same_time, error_count=ec)
            for ec in range(0, 7)  # 0-6，7 触 cap
        }
        for ec in range(1, 7):
            assert weights[ec] > weights[ec - 1], \
                f"weight should increase with error_count: ec={ec}"

    def test_weight_caps_at_max_factor(self):
        """error_count 足够大时权重被 max_factor cap"""
        w_low = compute_disposition_weight(last_error_at=None, error_count=20,
                                           max_factor=2.0)
        w_high = compute_disposition_weight(last_error_at=None, error_count=100,
                                            max_factor=2.0)
        assert w_low == pytest.approx(2.0)
        assert w_high == pytest.approx(2.0)
        assert w_low == w_high  # 都触 cap


# ─────────────────────────────────────────────────────────────────
# 运行入口
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
