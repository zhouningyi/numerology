from scripts.canon.process_daodejing import clean_original_line, parse_chapters
from scripts.canon.translate_daodejing import validate_annotations
from scripts.canon.align_daodejing_sentences import validate_pairs


def test_parse_chapters_excludes_wang_bi_commentary():
    text = """==一章==
道可道，非常道。
:{{*|可道之道，可名之名，指事造形，非其常也。}}
名可名，非常名。
==二章==
天下皆知美之为美，斯恶已。
:美者，人心之所进乐也。
"""
    assert parse_chapters(text) == [
        {"chapter": 1, "text": "道可道，非常道。\n名可名，非常名。"},
        {"chapter": 2, "text": "天下皆知美之为美，斯恶已。"},
    ]
    assert clean_original_line(":王弼注") == ""


def test_parse_chapters_excludes_postscript_after_last_chapter():
    text = """==八十一章==
信言不美，美言不信。
=跋=
这不是经文。
"""
    assert parse_chapters(text) == [{"chapter": 81, "text": "信言不美，美言不信。"}]


def test_annotations_require_exact_source_evidence():
    source = "上善若水。水善利万物而不争。"
    annotations = validate_annotations([
        {"tag": "柔弱", "evidence": "上善若水", "note": "以水作譬喻"},
        {"tag": "无为", "evidence": "与世无争", "note": "不是原文"},
        {"tag": "自创", "evidence": "水善", "note": "非法标签"},
    ], source)
    assert annotations == [{"tag": "柔弱", "evidence": "上善若水", "note": "以水作譬喻"}]


def test_sentence_pairs_require_the_exact_source_split():
    source = "上善若水。水善利万物而不争。"
    assert validate_pairs(source, [["上善若水。", "最高的善如同水。"], ["水善利万物而不争。", "水滋养万物却不争夺。"]])
    assert validate_pairs(source, [["上善若水", "遗漏句号"], ["水善利万物而不争。", "正常"]]) == []
