"""华严阅读页译文质量筛选。"""

from numerology.translation_display import (
    contains_web_junk,
    is_simplified_only,
    sanitize_translation_row,
    select_inline_translations,
)


def test_detects_web_junk_in_o6_style_blob():
    text = (
        "华严经是大乘佛教修学最..\n[详情]\n华严经\n原文\n译文\n"
        "白话华严经 第一卷\n作者：洪启嵩\n【原典】\n如是我闻：\n一时，佛在摩竭提国"
    )
    assert contains_web_junk(text)


def test_detects_simplified_only_not_vernacular():
    original = "爾時，世尊處于此座，於一切法成最正覺，智入三世悉皆平等。"
    fake = "尔时，世尊处于此座，于一切法成最正觉，智入三世悉皆平等。"
    real = "这时，世尊端坐在师子宝座上，在一切法当中成就了无上正等正觉。"
    assert is_simplified_only(original, fake)
    assert not is_simplified_only(original, real)


def test_sanitize_drops_junk_row():
    row = {
        "layer": "现代释译",
        "text": "华严经是大乘佛教修学最.. [详情] 放大字体",
        "original_segment_index": 6,
        "confidence": "high",
    }
    assert sanitize_translation_row(row, "一時，佛在。") is None


def test_select_prefers_aligned_baihua_over_junk_generated():
    originals = [
        {"segment_index": 5, "text": "如是我聞：", "layer": "原文"},
        {"segment_index": 6, "text": "一時，佛在摩竭提國阿蘭若法菩提場中，始成正覺。", "layer": "原文"},
        {"segment_index": 7, "text": "爾時，世尊處于此座，於一切法成最正覺。", "layer": "原文"},
    ]
    candidates = [
        {
            "layer": "现代释译",
            "original_segment_index": 6,
            "text": "华严经是大乘佛教修学最.. [详情] 作者：洪启嵩 【原典】 如是我闻： 一时，佛在",
            "translation_source": "洪启嵩译（模型仅对齐）",
            "alignment_method": "句级对齐提取，逐字取自译本；补译占比 0%",
            "confidence": "high",
        },
        {
            "layer": "现代白话",
            "original_segment_index": 5,
            "text": "这部经典是我阿难听闻佛陀的开示之后，如实宣说的。",
            "translation_source": "洪启嵩译",
            "alignment_method": "多模型逐段语义对齐",
        },
        {
            "layer": "现代白话",
            "original_segment_index": 6,
            "text": "当时，佛陀正安坐在摩竭提国的阿兰若正法菩提道场中，他刚刚成就了无上正等正觉。",
            "translation_source": "洪启嵩译",
        },
        {
            "layer": "现代释译",
            "original_segment_index": 7,
            "text": "尔时，世尊处于此座，于一切法成最正觉。",
            "translation_source": "洪启嵩译（模型仅对齐）",
        },
        {
            "layer": "现代白话",
            "original_segment_index": 7,
            "text": "这时，世尊端坐在师子宝座上，在一切法当中成就了无上正等正觉。",
            "translation_source": "洪启嵩译",
        },
    ]
    selected = select_inline_translations(originals, candidates)
    by_oi = {row["original_segment_index"]: row for row in selected}
    assert 5 in by_oi
    assert "这部经典" in by_oi[5]["text"]
    assert 6 in by_oi
    assert "当时，佛陀" in by_oi[6]["text"]
    assert "[详情]" not in by_oi[6]["text"]
    assert 7 in by_oi
    assert "这时，世尊" in by_oi[7]["text"]
