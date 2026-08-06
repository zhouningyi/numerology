"""共现分析统计量测试。"""

import math

import pytest

from scripts.nde.analyze_cooccurrence import benjamini_hochberg, chi2_p_value, pair_stats


def test_pair_stats_perfect_association():
    docs = {f"d{i}" for i in range(100)}
    a = {f"d{i}" for i in range(50)}
    stat = pair_stats(a, set(a), len(docs))
    assert stat["phi"] == pytest.approx(1.0, abs=1e-6)
    assert stat["lift"] == pytest.approx(2.0)          # 0.5 独立期望 → 实际全同现
    assert stat["p"] < 0.001


def test_pair_stats_independent_is_lift_one():
    total = 400
    a = {f"d{i}" for i in range(200)}                  # 前一半
    b = {f"d{i}" for i in range(0, 400, 2)}           # 偶数 → 与 a 独立
    stat = pair_stats(a, b, total)
    assert stat["lift"] == pytest.approx(1.0, abs=0.05)
    assert abs(stat["phi"]) < 0.05


def test_pair_stats_mutual_exclusion():
    total = 100
    a = {f"d{i}" for i in range(50)}
    b = {f"d{i}" for i in range(50, 100)}
    stat = pair_stats(a, b, total)
    assert stat["both"] == 0
    assert stat["lift"] == 0.0
    assert stat["phi"] < 0


def test_chi2_p_value_monotonic():
    assert chi2_p_value(0) == pytest.approx(1.0)
    assert chi2_p_value(3.84) == pytest.approx(0.05, abs=0.005)   # 临界值
    assert chi2_p_value(10.83) < 0.002


def test_benjamini_hochberg_controls_discoveries():
    # 3 个极显著 + 7 个不显著
    pvals = [1e-6, 1e-5, 1e-4] + [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
    flags = benjamini_hochberg(pvals, alpha=0.05)
    assert flags[:3] == [True, True, True]
    assert not any(flags[3:])


def test_benjamini_hochberg_all_null():
    assert not any(benjamini_hochberg([0.2, 0.5, 0.9], alpha=0.05))
