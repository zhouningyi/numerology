"""叙述证据高亮与问卷行标记测试。"""

from server import (
    build_nde_tag_groups, highlight_evidence, normalize_nde_date,
    prepare_nde_rows, tag_qa_rows,
)


def test_highlight_wraps_exact_evidence():
    text = "I left my body. Time had no meaning there. Then I returned."
    html = highlight_evidence(text, [
        {"name": "时间虚幻", "evidence": "Time had no meaning there."},
    ])
    assert '<mark class="ev-mark" title="概念证据：时间虚幻">Time had no meaning there.</mark>' in html
    assert html.startswith("I left my body. ")


def test_highlight_falls_back_to_prefix_and_escapes():
    text = "The light said <hello> and everything made sense to me at once."
    html = highlight_evidence(text, [
        # 模型摘句轻微改写：结尾不同 → 前 40 字符前缀命中
        {"name": "直接知识", "evidence": "The light said <hello> and everything made sense IMMEDIATELY"},
    ])
    assert "&lt;hello&gt;" in html          # 转义仍生效
    assert "ev-mark" in html


def test_highlight_unmatched_evidence_is_ignored():
    html = highlight_evidence("Nothing here.", [{"name": "x", "evidence": "completely different sentence"}])
    assert "ev-mark" not in html
    assert html == "Nothing here."


def test_fulltext_query_is_highlighted_as_an_exact_phrase():
    html = highlight_evidence("Bright light, then a bright light.", [], "bright light")
    assert html.count('class="search-mark"') == 2
    assert "Bright light" in html
    assert "bright light" in html


def test_nde_date_display_is_normalized_to_year_month():
    assert normalize_nde_date("03/11/2001") == "2001-03"
    assert normalize_nde_date("1st August 2015") == "2015-08"
    assert normalize_nde_date("2026") == "2026"


def test_tag_qa_rows_marks_positive_rows():
    phenomena = {
        "tunnel": {"name": "隧道", "match": [{"question_contains": "through a tunnel"}]},
        "deceased": {"name": "已故亲友", "match": [{"question_contains": "deceased"}]},
    }
    qa = [
        {"q": "Did you pass into or through a tunnel", "a": "Yes, a long one"},
        {"q": "Did you encounter any deceased beings", "a": "No"},
    ]
    tagged = tag_qa_rows(qa, phenomena)
    assert tagged == {0: ["隧道"]}


def test_selected_concepts_limit_body_highlights():
    rows, _ = prepare_nde_rows([
        {
            "slug": "sample",
            "description": "Time had no meaning. I knew everything at once.",
            "translations": {"中文": "时间没有意义。我同时知道了一切。"},
            "concepts": {
                "time_illusion": "Time had no meaning.",
                "direct_knowing": "I knew everything at once.",
            },
            "concepts_zh": {},
        }
    ], {
        "time_illusion": {"name": "时间虚幻"},
        "direct_knowing": {"name": "直接知识"},
    }, ["time_illusion"])
    assert 'data-concept="time_illusion"' in rows[0]["description_html"]
    assert "data-concept=\"direct_knowing\"" not in rows[0]["description_html"]


def test_nde_tag_groups_include_phenomenon_and_idea_tags():
    groups = build_nde_tag_groups(
        {
            "bright_light": {"name": "强光/白光"},
            "deceased": {"name": "已故亲友"},
        },
        {"time_illusion": {"name": "时间虚幻"}},
        [
            {
                "categories": {"bright_light": "Yes", "deceased": "Yes"},
                "concepts": {"time_illusion": "evidence"},
            }
        ],
    )
    assert [group["name"] for group in groups] == ["现象", "理念", "亡灵"]
    assert groups[0]["tags"][0]["value"] == "category:bright_light"
    assert groups[1]["tags"][0]["value"] == "concept:time_illusion"
    assert groups[2]["tags"][0]["value"] == "category:deceased"
