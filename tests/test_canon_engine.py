"""穷通宝鉴规则引擎测试。"""

from numerology.canon.engine import QiongtongEngine

RULES = [
    {
        "rule_id": "qiongtong_甲_寅",
        "rule_status": "verified",
        "if": ["日干 == 甲", "月支 == 寅"],
        "then": [{"调候用神候选": ["丁", "庚"]}],
        "verified_stems": ["丙", "癸"],
    },
    {
        "rule_id": "qiongtong_甲_卯",
        "rule_status": "candidate",
        "if": ["日干 == 甲", "月支 == 卯"],
        "then": [{"调候用神候选": ["庚"]}],
    },
]


def _chart(**kw):
    base = {
        "year_pillar": "丙子", "month_pillar": "庚寅",
        "day_pillar": "甲午", "time_pillar": "癸酉", "day_master": "甲",
    }
    base.update(kw)
    return base


def test_verified_stems_take_precedence_over_candidates():
    engine = QiongtongEngine(rules=RULES)
    feats = engine.features(_chart())
    assert feats["qt_stems"] == "丙癸"       # 用核定序列而非脚本候选
    assert feats["qt_primary"] == "丙"
    assert feats["qt_primary_tou"] == 1      # 丙透于年干
    assert feats["qt_tou_count"] == 2        # 丙、癸皆透


def test_candidate_rules_excluded_by_default():
    engine = QiongtongEngine(rules=RULES)
    assert engine.rule_count == 1
    assert engine.features(_chart(month_pillar="己卯")) is None


def test_candidate_rules_included_when_smoke_testing():
    engine = QiongtongEngine(rules=RULES, statuses=("verified", "candidate"))
    feats = engine.features(_chart(month_pillar="己卯"))
    assert feats["qt_rule_id"] == "qiongtong_甲_卯"
    assert feats["qt_rule_status"] == "candidate"


def test_missing_time_pillar_narrows_visible_stems():
    engine = QiongtongEngine(rules=RULES)
    feats = engine.features(_chart(time_pillar=None, year_pillar="壬子"))
    assert feats["n_visible_stems"] == 2
    assert feats["qt_primary_tou"] == 0      # 丙不再可见
    # 藏支统计仍覆盖年月日三柱：寅藏丙
    assert feats["qt_primary_cang"] == 1


def test_hidden_stems_counted_from_canggan():
    engine = QiongtongEngine(rules=RULES)
    # 子藏癸：主用神丙不藏，癸藏于年支
    feats = engine.features(_chart(year_pillar="乙子", time_pillar="乙丑"))
    assert feats["qt_cang_count"] >= 1
