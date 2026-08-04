"""华严句句强制对齐。"""

from numerology.huayan_sentence_align import (
    force_align_sentences,
    lexical_score,
    split_classic_sentences,
    clean_reference_text,
)


def test_split_classic_sentences():
    text = "如是我聞：一時，佛在摩竭提國。爾時世尊成最正覺。"
    sents = split_classic_sentences(text)
    assert any("如是我聞" in s for s in sents)
    assert any("佛在摩竭提國" in s for s in sents)


def test_clean_reference_drops_nav():
    raw = "华严经是大乘佛教修学最..\n[详情]\n这部经典是我阿难听闻佛陀的开示之后，如实宣说的。"
    cleaned = clean_reference_text(raw)
    assert "[详情]" not in cleaned
    assert "这部经典" in cleaned


def test_force_align_ruyishiwen_to_vernacular():
    orig = ["如是我聞：", "一時，佛在摩竭提國阿蘭若法菩提場中，始成正覺。"]
    ref = [
        "这部经典是我阿难听闻佛陀的开示之后，如实宣说的。",
        "当时，佛陀正安坐在摩竭提国的阿兰若正法菩提道场中，他刚刚成就了无上正等正觉圆满菩提道而成佛。",
        "这座正法菩提道场是以金刚作为地基造就而成的。",
    ]
    result = force_align_sentences(orig, ref)
    assert len(result.pairs) == 2
    assert "这部经典" in result.pairs[0][1] or "听闻" in result.pairs[0][1]
    assert "佛陀" in result.pairs[1][1] or "摩竭提" in result.pairs[1][1]
    assert not result.pairs[0][1].startswith("华严经是大乘")


def test_lexical_prefers_true_vernacular_over_simplified():
    o = "爾時，世尊處于此座，於一切法成最正覺。"
    fake = "尔时，世尊处于此座，于一切法成最正觉。"
    real = "这时，世尊端坐在师子宝座上，在一切法当中成就了无上正等正觉。"
    assert lexical_score(o, real) > lexical_score(o, fake)
