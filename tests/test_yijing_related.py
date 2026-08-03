"""《周易》跨书聚合测试。"""

from aggregate_yijing_related import dongpo_hexagram_name, yijing_hexagram_name


def test_yijing_hexagram_name_is_stable():
    assert yijing_hexagram_name("第一卦 乾 乾为天 乾上乾下") == "乾"
    assert yijing_hexagram_name("第二十八卦 大过 泽风大过 兑上巽下") == "大过"


def test_dongpo_hexagram_name_ignores_layout_spaces():
    assert dongpo_hexagram_name("东坡易传：同 人 卦") == "同人"
    assert dongpo_hexagram_name("东坡易传：既 济 卦") == "既济"
