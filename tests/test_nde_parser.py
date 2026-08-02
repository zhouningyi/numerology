"""NDERF 案例解析与现象分类测试。"""

from numerology.nde.parser import classify, html_to_lines, parse_experience

SAMPLE_HTML = """
<html><head><title>Test P NDE 12345 | NDERF</title></head><body>
<div>Classification</div><div>NDE</div>
<div>Experience Description</div>
<p>I left my body and floated above the operating table.</p>
<p>Then I saw a brilliant white light at the end of a tunnel.</p>
<div>Background Information:</div>
<div>Gender:</div><div>Male</div>
<div>Did you feel separated from your body?</div>
<div>I clearly left my body and existed outside it</div>
<div>Did you pass into or through a tunnel?</div>
<div>Yes, a long dark tunnel</div>
<div>Did you see an unearthly light?</div>
<div>Yes</div>
<div>Did you encounter or become aware of any deceased (or alive) beings?</div>
<div>No</div>
<div>Did you seem to enter some other, unearthly world?</div>
<div>Uncertain</div>
</body></html>
"""


def test_parse_experience_extracts_structure():
    record = parse_experience(
        "https://www.nderf.org/Experiences/test_p_nde_12345.htm", SAMPLE_HTML
    )
    assert record["slug"] == "test_p_nde_12345"
    assert record["title"] == "Test P NDE 12345"
    assert record["classification"] == "NDE"
    assert "floated above" in record["description"]
    assert "Background" not in record["description"]
    questions = [pair["q"] for pair in record["qa"]]
    assert "Did you pass into or through a tunnel" in questions


def test_classification_uses_survey_answers():
    record = parse_experience(
        "https://www.nderf.org/Experiences/test_p_nde_12345.htm", SAMPLE_HTML
    )
    cats = record["categories"]
    assert "obe" in cats            # 明确离体
    assert "tunnel" in cats         # Yes
    assert "bright_light" in cats   # unearthly light: Yes
    assert "deceased" not in cats   # No 为否定
    assert "other_world" not in cats  # Uncertain 为否定


def test_negative_prefixes_are_rejected():
    qa = [{"q": "Did you pass into or through a tunnel", "a": "No tunnel at all"}]
    assert classify(qa) == {}
    qa = [{"q": "Did you pass into or through a tunnel", "a": "Yes indeed"}]
    assert "tunnel" in classify(qa)


def test_html_to_lines_strips_scripts():
    lines = html_to_lines("<script>var x=1;</script><p>keep me</p>")
    assert lines == ["keep me"]


def test_broken_page_returns_none():
    assert parse_experience("https://x/no_desc.htm", "<html><body>nothing</body></html>") is None
