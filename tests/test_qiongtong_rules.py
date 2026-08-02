"""穷通宝鉴调候规则抽取的解析测试。"""

from pathlib import Path

import pytest

from extract_qiongtong_rules import candidate_stems, first_canon_line, parse_title


def test_parse_title_simple():
    assert parse_title("正月甲木") == (["正"], "甲")
    assert parse_title("十一月癸水") == (["十一"], "癸")


def test_parse_title_merged_months():
    assert parse_title("五、六月甲木") == (["五", "六"], "甲")
    assert parse_title("正二月戊土") == (["正", "二"], "戊")
    assert parse_title("十十一十二月己土") == (["十", "十一", "十二"], "己")
    assert parse_title("四五六月己土") == (["四", "五", "六"], "己")


def test_parse_title_rejects_non_month_chapters():
    assert parse_title("甲木总论") is None
    assert parse_title("论土") is None


def test_candidate_stems_excludes_day_stem_and_dedups():
    line = "正月甲木，初春尚有余寒，得丙癸逢，富贵双全。癸藏丙透，名寒木向阳。"
    assert candidate_stems(line, "甲") == ["丙", "癸"]


def test_first_canon_line_skips_commentary():
    segments = [
        {"layer": "原文", "text": "徐乐吾曰：评注内容。\n正月甲木，得丙癸逢。"},
        {"layer": "现代白话", "text": "白话译文。"},
    ]
    assert first_canon_line(segments) == "正月甲木，得丙癸逢。"


@pytest.mark.skipif(
    not Path("numerology/canon/schools/qiongtong.yaml").exists(),
    reason="规则文件未生成",
)
def test_generated_rules_cover_120_cells():
    import yaml

    data = yaml.safe_load(
        Path("numerology/canon/schools/qiongtong.yaml").read_text(encoding="utf-8")
    )
    rules = data["rules"]
    cells = {(r["if"][0], r["if"][1]) for r in rules}
    assert len(rules) == 120
    assert len(cells) == 120
    for rule in rules:
        assert rule["rule_status"] == "candidate"
        assert rule["quote"]
