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


def test_tag_motifs_finds_white_light():
    from numerology.nde.parser import tag_motifs
    text = "I floated upward and saw a brilliant white light that loved me."
    motifs = tag_motifs(text)
    assert "white_light" in motifs
    assert "white light" in motifs["white_light"].lower() or "brilliant white" in motifs["white_light"].lower()


def test_negative_prefixes_are_rejected():
    qa = [{"q": "Did you pass into or through a tunnel", "a": "No tunnel at all"}]
    assert classify(qa) == {}
    qa = [{"q": "Did you pass into or through a tunnel", "a": "Yes indeed"}]
    assert "tunnel" in classify(qa)


def test_negative_prefix_beats_positive_contains():
    """否定应答优先于 positive_contains，避免 No…acquire 误阳。"""
    qa = [{
        "q": "Did you have any psychic, paranormal or other special gifts",
        "a": "No, I did not acquire any gifts",
    }]
    assert "aftereffects_gifts" not in classify(qa)


def test_no_longer_is_not_negative_answer():
    from numerology.nde.parser import _is_negative_answer
    assert _is_negative_answer("No, I did not")
    assert _is_negative_answer("Uncertain")
    assert not _is_negative_answer("No longer attached to my body")
    assert not _is_negative_answer("Yes, I left my body")



def test_html_to_lines_strips_scripts():
    lines = html_to_lines("<script>var x=1;</script><p>keep me</p>")
    assert lines == ["keep me"]


def test_legacy_format_with_nbsp_colon_marker():
    html = SAMPLE_HTML.replace(
        "<div>Experience Description</div>",
        "<div>Experience Description\xa0:</div>",
    )
    record = parse_experience("https://x/legacy_nde.htm", html)
    assert record is not None
    assert "floated above" in record["description"]


def test_broken_page_returns_none():
    assert parse_experience("https://x/no_desc.htm", "<html><body>nothing</body></html>") is None
