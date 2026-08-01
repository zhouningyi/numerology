"""古籍分层标注的状态机测试。"""

from process_canon_layers import merge_segments, tag_lines


def _segments(lines, markers=("徐注",)):
    return merge_segments("test", tag_lines(lines, list(markers)))


def test_explicit_marker_switches_to_commentary_with_high_confidence():
    lines = [
        "第 10 章",
        "原 文",
        "八字用神，专求月令。",
        "**【徐注】**用神者，八字中所用之神也。",
        "所取用神未真，命无准理。",
    ]
    segs = _segments(lines)
    assert [(s["layer"], s["confidence"]) for s in segs] == [
        ("原文", "low"),
        ("评注", "high"),
        ("评注", "low"),
    ]
    assert segs[1]["marker"] == "徐注"
    assert all(s["chapter"] == 10 for s in segs)


def test_site_text_label_returns_to_original_layer_with_low_confidence():
    lines = [
        "**【徐注】**评注内容。",
        "原文",
        "取用之法不一，约略归纳。",
    ]
    segs = _segments(lines)
    # 网页"原文"标签不区分原著与评注，只能回到原文层且置信为 low
    assert (segs[-1]["layer"], segs[-1]["confidence"]) == ("原文", "low")


def test_baihua_and_site_sections_are_tagged():
    lines = [
        "原 文",
        "欲识三元万法宗。",
        "白话译文",
        "想要通晓命理学中三元统摄的法则。",
        "关键词",
        "三元 天元、地元、人元的合称。",
    ]
    layers = [s["layer"] for s in _segments(lines)]
    assert layers == ["原文", "现代白话", "站点内容"]


def test_renshi_marker_and_page_reset_for_ditiansui():
    lines = [
        "第 三 页",
        "欲识三元万法宗，先观帝载与神功。",
        "**【原注】**天有阴阳，故春木、夏火。",
        "【任氏曰】：",
        "干为天元，支为地元。",
        "第 四 页",
        "坤元合德机缄通。",
    ]
    segs = _segments(lines, markers=("任氏曰",))
    assert [(s["layer"], s["confidence"]) for s in segs] == [
        ("原文", "low"),
        ("原注", "high"),
        ("评注", "high"),
        ("评注", "low"),
        ("原文", "low"),
    ]


def test_boilerplate_lines_are_dropped():
    lines = [
        "典 古籍典藏 luckclub.cn 目录 八字 中医 易经 风水",
        "复制链接",
        "字号 小 中 大",
        "正文内容。",
    ]
    segs = _segments(lines)
    assert len(segs) == 1
    assert segs[0]["text"] == "正文内容。"
