"""叙述证据高亮与问卷行标记测试。"""

from server import highlight_evidence, tag_qa_rows


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
