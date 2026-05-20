"""
tests/unit/test_l1_search.py
=====================================
单元测试：l1_search.py B8 公式

覆盖：
- calculate_activation_score()  — sim × f_time × min(error_count, cap)
"""

import sys, os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "phase3"))

import pytest
import datetime as _dt
from impl.l1_search import calculate_activation_score


# ─────────────────────────────────────────────────────────────────
# B8: sim × f_time × capped_error_count
# ─────────────────────────────────────────────────────────────────

class TestActivationScore_ErrorFactor:
    """error_count cap 对 score 的影响"""

    def test_error_count_zero_suppresses(self):
        """error_count=0 → capped=0 → score=0"""
        score, f_time, ef = calculate_activation_score(
            sim=0.8, error_count=0, last_error_at=None
        )
        assert ef == 0
        assert score == 0.0

    def test_error_count_one_neutral(self):
        """error_count=1 → capped=1 → score = sim × f_time"""
        score, f_time, ef = calculate_activation_score(
            sim=0.8, error_count=1, last_error_at=None
        )
        assert ef == 1
        assert score == pytest.approx(0.8)

    def test_error_count_capped(self):
        """error_count 超过 cap 时被截断"""
        score5, _, ef5 = calculate_activation_score(
            sim=0.8, error_count=5, last_error_at=None
        )
        score100, _, ef100 = calculate_activation_score(
            sim=0.8, error_count=100, last_error_at=None
        )
        assert ef5 == 5
        assert ef100 == 5  # cap = 5
        assert score5 == score100  # capped at same level

    def test_error_count_below_cap(self):
        """error_count 在 cap 内，完全参与计算"""
        score3, _, ef3 = calculate_activation_score(
            sim=0.7, error_count=3, last_error_at=None
        )
        score4, _, ef4 = calculate_activation_score(
            sim=0.7, error_count=4, last_error_at=None
        )
        assert ef3 == 3
        assert ef4 == 4
        assert score4 > score3


class TestActivationScore_TimeDecay:
    """f_time 衰减对 score 的影响"""

    def test_no_last_error_fresh(self):
        """last_error_at=None → f_time=1.0"""
        score, f_time, _ = calculate_activation_score(
            sim=0.9, error_count=3, last_error_at=None
        )
        assert f_time == 1.0
        assert score == pytest.approx(2.7, rel=0.01)  # 0.9 * 1.0 * 3

    def test_half_life_decay(self):
        """半衰期后 f_time ≈ 0.5"""
        half_life = 7.0
        ago = (_dt.datetime.now() - _dt.timedelta(days=half_life)).isoformat()
        _, f_time, _ = calculate_activation_score(
            sim=1.0, error_count=1, last_error_at=ago, half_life_days=half_life
        )
        assert f_time == pytest.approx(0.5, abs=0.02)

    def test_double_half_life(self):
        """2倍半衰期后 f_time ≈ 0.25"""
        half_life = 7.0
        ago = (_dt.datetime.now() - _dt.timedelta(days=half_life * 2)).isoformat()
        _, f_time, _ = calculate_activation_score(
            sim=1.0, error_count=1, last_error_at=ago, half_life_days=half_life
        )
        assert f_time == pytest.approx(0.25, abs=0.02)

    def test_invalid_timestamp_fallback(self):
        """无效时间戳 → f_time=1.0"""
        _, f_time, _ = calculate_activation_score(
            sim=0.8, error_count=2, last_error_at="not-a-date"
        )
        assert f_time == 1.0


class TestActivationScore_Combined:
    """三个因子联合作用"""

    def test_sim_dominates_when_time_fresh(self):
        """近期高频 × 高相似度"""
        recent = (_dt.datetime.now() - _dt.timedelta(days=1)).isoformat()
        score = calculate_activation_score(
            sim=0.9, error_count=5, last_error_at=recent
        )[0]
        # 0.9 * ~0.9 * 5 ≈ 4.05
        assert score > 3.5

    def test_old_low_sim_minimal(self):
        """远期 × 低频 × 低相似度"""
        old = (_dt.datetime.now() - _dt.timedelta(days=30)).isoformat()
        score = calculate_activation_score(
            sim=0.3, error_count=0, last_error_at=old
        )[0]
        assert score == 0.0  # error_count=0 caps to 0

    def test_score_increases_with_error_count(self):
        """相同 sim + 时间，error_count 越高 score 越高"""
        same_time = (_dt.datetime.now() - _dt.timedelta(days=3)).isoformat()
        scores = [
            calculate_activation_score(sim=0.7, error_count=ec, last_error_at=same_time)[0]
            for ec in range(0, 6)
        ]
        for i in range(1, 5):  # 0→1, 1→2, 2→3, 3→4, 4→5
            assert scores[i] > scores[i - 1], f"ec={i} should beat ec={i-1}"

    def test_score_increases_with_time(self):
        """相同 sim + error_count，越近 score 越高"""
        base = (sim, ec) = (0.8, 3)
        scores = {}
        for days in [0, 3, 7, 14]:
            t = (_dt.datetime.now() - _dt.timedelta(days=days)).isoformat()
            scores[days] = calculate_activation_score(sim=sim, error_count=ec, last_error_at=t)[0]
        for d in [3, 7, 14]:
            assert scores[0] > scores[d], f"fresh should beat {d}-day-old"

    def test_score_increases_with_similarity(self):
        """相同时间 + error_count，sim 越高 score 越高"""
        same_time = (_dt.datetime.now() - _dt.timedelta(days=5)).isoformat()
        for s1, s2 in [(0.5, 0.7), (0.7, 0.9), (0.9, 1.0)]:
            score1 = calculate_activation_score(sim=s1, error_count=2, last_error_at=same_time)[0]
            score2 = calculate_activation_score(sim=s2, error_count=2, last_error_at=same_time)[0]
            assert score2 > score1, f"sim={s2} should beat sim={s1}"


class TestActivationScore_Boundaries:
    """边界值"""

    def test_sim_zero_returns_zero(self):
        """sim=0 → score=0"""
        score, _, _ = calculate_activation_score(
            sim=0.0, error_count=5, last_error_at=None
        )
        assert score == 0.0

    def test_sim_negative_unchanged(self):
        """负相似度数学上可能（取决于 cosine_sim），函数不拦截"""
        score, _, _ = calculate_activation_score(
            sim=-0.1, error_count=2, last_error_at=None
        )
        assert score < 0  # 负 sim 得负分， caller 负责过滤

    def test_large_error_count_capped(self):
        """极大 error_count 被 cap"""
        score, _, ef = calculate_activation_score(
            sim=1.0, error_count=9999, last_error_at=None, max_error_count_cap=5
        )
        assert ef == 5
        assert score == pytest.approx(5.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
