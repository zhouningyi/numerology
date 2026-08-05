"""标签体系归纳的纯函数测试（不调用 API）。"""

import pytest

from numerology.nde.taxonomy import (
    agreement_report,
    audit_taxonomy,
    batched,
    dedupe_phrases,
    jaccard,
    krippendorff_alpha_binary,
    normalize_phrase,
    plan_merge_rounds,
    select_residual,
    split_paragraphs,
)


def test_split_paragraphs_merges_short_and_splits_long():
    text = "短.\n\n" + "A" * 300 + "\n\n" + ("句子。" * 5 + "x" * 1600)
    parts = split_paragraphs(text, min_chars=120, max_chars=800)
    assert all(len(p) <= 900 for p in parts)      # 超长段被切开
    assert all(len(p) >= 40 for p in parts)       # 碎片被过滤或并入


def test_plan_merge_rounds_converges():
    plan = plan_merge_rounds(20000, batch_size=200, compress_ratio=10, target=120)
    assert plan[0]["input"] == 20000
    assert plan[0]["batches"] == 100
    # 每轮必须压缩，且最终收敛到目标附近
    inputs = [step["input"] for step in plan]
    assert inputs == sorted(inputs, reverse=True)
    assert plan[-1]["expected"] <= max(200, 120 * 2)


def test_plan_merge_rounds_stops_when_already_small():
    assert plan_merge_rounds(80, target=120) == []


def test_batched_covers_all_items():
    items = list(range(45))
    chunks = batched(items, 20)
    assert [len(c) for c in chunks] == [20, 20, 5]
    assert [x for c in chunks for x in c] == items


def test_normalize_and_dedupe_counts_variants():
    phrases = ["being pulled toward the light", "Being pulled toward light",
               "my body floating above", "body floating above"]
    result = dedupe_phrases(phrases)
    assert result[0][1] == 2                       # 两种写法归为一项、频次 2
    assert len(result) == 2
    assert normalize_phrase("The Bright Light!") == "bright light"


def test_krippendorff_perfect_and_total_disagreement():
    perfect = [(1, 1)] * 5 + [(0, 0)] * 5
    assert krippendorff_alpha_binary(perfect) == pytest.approx(1.0)
    total = [(1, 0)] * 5 + [(0, 1)] * 5
    assert krippendorff_alpha_binary(total) < 0     # 完全相反 → 负值
    assert krippendorff_alpha_binary([(1, 1)] * 5) is None  # 无变异 → 未定义


def test_krippendorff_partial_agreement_between_bounds():
    pairs = [(1, 1)] * 8 + [(0, 0)] * 8 + [(1, 0)] * 2 + [(0, 1)] * 2
    alpha = krippendorff_alpha_binary(pairs)
    assert 0.5 < alpha < 0.95


def test_agreement_report_shape():
    first = {"a": {"x"}, "b": {"x", "y"}, "c": set()}
    second = {"a": {"x"}, "b": {"y"}, "c": set()}
    report = agreement_report(first, second, ["x", "y"])
    assert report["x"]["hits_first"] == 2 and report["x"]["hits_second"] == 1
    assert report["y"]["agreement"] == pytest.approx(1.0)


def test_audit_flags_broad_rare_and_redundant():
    docs = {f"d{i}": set() for i in range(400)}
    for i in range(400):
        docs[f"d{i}"].add("everywhere")            # 100% → 过宽
    for i in range(360):
        docs[f"d{i}"].add("twin")                  # 与 everywhere 高度共现
    docs["d0"].add("rare_one")                     # 0.25% → 过窄（阈值 0.5%）
    audit = audit_taxonomy(docs)
    assert "everywhere" in audit["too_broad"]
    assert "rare_one" in audit["too_rare"]
    pairs = {tuple(sorted(p["pair"])) for p in audit["redundant_pairs"]}
    assert ("everywhere", "twin") in pairs
    assert audit["coverage"] == pytest.approx(1.0)


def test_audit_reports_uncovered_and_coverage():
    docs = {"a": {"x"}, "b": set(), "c": set()}
    audit = audit_taxonomy(docs)
    assert audit["coverage"] == pytest.approx(1 / 3)
    assert set(audit["uncovered"]) == {"b", "c"}


def test_select_residual_returns_zero_label_docs():
    docs = {"a": {"x"}, "b": set(), "c": set()}
    assert select_residual(docs) == ["b", "c"]


def test_jaccard_basic():
    assert jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)
    assert jaccard(set(), set()) == 0.0
