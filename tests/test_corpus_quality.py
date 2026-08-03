"""语料翻译/映射质量：状态、section_key、挂接策略、现象分类。"""

from numerology.corpus_quality import (
    REVIEW_CANDIDATE,
    REVIEW_HUMAN_VERIFIED,
    REVIEW_MODEL_AGREE,
    apply_quality_fields,
    confidence_for_review,
    normalize_generated_huayan_rows,
    normalize_section_key,
    resolve_inline_alignment,
)
from numerology.nde.parser import classify


def test_normalize_section_key_strips_parenthetical():
    assert normalize_section_key("文言传（节要）") == "文言传"
    assert normalize_section_key("初九") == "初九"
    assert normalize_section_key("彖") == "彖传"


def test_confidence_high_only_after_human_verified():
    assert confidence_for_review(REVIEW_CANDIDATE, "high") == "low"
    assert confidence_for_review(REVIEW_MODEL_AGREE, "high") == "medium"
    assert confidence_for_review(REVIEW_HUMAN_VERIFIED, "low") == "high"


def test_apply_quality_fields_downgrades_missing_provenance_high():
    row = {
        "layer": "现代释译",
        "confidence": "high",
        "alignment_status": "已对齐",
        "text": "白话",
        "original_segment_index": 5,
    }
    fixed = apply_quality_fields(row)
    assert fixed["review_status"] == REVIEW_CANDIDATE
    assert fixed["confidence"] == "low"


def test_normalize_generated_legacy_rows():
    rows = [{
        "layer": "现代释译",
        "confidence": "high",
        "alignment_status": "已对齐",
        "segment_index": 12,
        "text": "…",
        "translation_source": "历史层",
    }]
    fixed = normalize_generated_huayan_rows(rows)
    assert fixed[0]["confidence"] == "low"
    assert fixed[0]["review_status"] == REVIEW_CANDIDATE
    assert fixed[0]["original_segment_indices"] == [12]
    assert fixed[0]["provenance"]["pipeline"]


def test_resolve_inline_prefers_segment_index():
    originals = [
        {"segment_index": 10, "section_key": "卦辞", "text": "甲"},
        {"segment_index": 11, "section_key": "初九", "text": "乙"},
    ]
    inline = [
        {"original_segment_index": 11, "text": "乙译", "layer": "现代白话"},
        {"original_segment_indices": [10], "text": "甲译", "layer": "现代白话"},
    ]
    result = resolve_inline_alignment(originals, inline)
    assert result["method"] == "original_segment_index"
    assert result["inline_by_original"][0][0]["text"] == "甲译"
    assert result["inline_by_original"][1][0]["text"] == "乙译"


def test_resolve_inline_uses_normalized_section_key_and_rejects_equal_length():
    originals = [
        {"segment_index": 1, "section_key": "卦辞", "text": "甲"},
        {"segment_index": 2, "section_key": "初九", "text": "乙"},
    ]
    # 无段号、键不匹配、数量相等 → 不得静默一对一
    inline = [
        {"section_key": None, "text": "x", "layer": "现代白话"},
        {"section_key": None, "text": "y", "layer": "现代白话"},
    ]
    result = resolve_inline_alignment(originals, inline)
    assert result["method"] == "unmatched_no_equal_length"
    assert len(result["unmatched_inline"]) == 2

    keyed = [
        {"section_key": "文言传（节要）", "text": "文", "layer": "现代白话"},
    ]
    originals2 = [
        {"segment_index": 1, "section_key": "文言传", "text": "文言曰"},
    ]
    result2 = resolve_inline_alignment(originals2, keyed)
    assert result2["method"] == "section_key"
    assert result2["inline_by_original"][0][0]["text"] == "文"


def test_aftereffects_gifts_accepts_yes_and_rejects_no_acquire():
    yes_qa = [{
        "q": "Did you have any psychic, paranormal or other special gifts following the experience that you did not have prior to the experience?",
        "a": "Yes, I developed a strong sense of intuition",
    }]
    no_qa = [{
        "q": "Did you have any psychic, paranormal or other special gifts following the experience?",
        "a": "No, I did not acquire any special gifts",
    }]
    assert "aftereffects_gifts" in classify(yes_qa)
    assert "aftereffects_gifts" not in classify(no_qa)


def test_negative_prefix_does_not_kill_no_longer():
    """“no longer …” 不是否定应答，不应被 no 词边界误杀。"""
    qa = [{
        "q": "Did you feel separated from your body?",
        "a": "No longer attached — I left my body and floated above",
    }]
    assert "obe" in classify(qa)
