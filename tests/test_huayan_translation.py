"""华严经小段独立翻译流程测试。"""

from translate_huayan_segments import build_prompt, split_source_units


def test_short_original_stays_one_translation_unit():
    text = "如是我聞："
    assert split_source_units(text, max_chars=100) == [text]


def test_long_original_splits_at_sentence_boundary():
    text = "甲" * 80 + "。" + "乙" * 80 + "。" + "丙" * 80
    units = split_source_units(text, max_chars=100)
    assert len(units) == 3
    assert "。" in units[0]
    assert "。" in units[1]
    assert "丙" in units[2]
    assert "".join(units) == text


def test_prompt_forbids_cross_segment_expansion():
    prompt = build_prompt("一时，佛在道场。")
    assert "只处理这段文字" in prompt
    assert "不补写上下文" in prompt
    assert '"translation"' in prompt
