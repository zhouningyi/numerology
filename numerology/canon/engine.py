"""古籍规则引擎：把校勘通过的规则解释为命盘特征。

第一个流派是穷通宝鉴（T1 查表型）：(日干 × 月支) → 调候用神序列。
引擎只做确定性求值，不含任何可调参数；规则来源、状态与版本全部可回溯。
默认只加载 rule_status == 'verified' 的规则——candidate 只能用于流水线冒烟，
产出的特征文件必须带 candidate 标记，禁止进入正式分析（实施计划 P2.5/P3）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

CANON_DIR = Path(__file__).parent
STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"


def load_canggan() -> dict[str, list[str]]:
    """地支 → 藏干列表（按本气/中气/余气顺序）。"""
    raw = yaml.safe_load(
        (CANON_DIR / "tables" / "canggan.yaml").read_text(encoding="utf-8")
    )["hidden_stems"]
    return {zhi: [item["gan"] for item in items] for zhi, items in raw.items()}


def _parse_condition(cond: str) -> tuple[str, str]:
    """解析形如“日干 == 甲”的等值条件。"""
    field, _, value = cond.partition("==")
    return field.strip(), value.strip()


class QiongtongEngine:
    """穷通宝鉴调候查表引擎。

    特征全部围绕一个问题：命盘里有没有该格所需的调候用神，以什么形式存在。
    """

    def __init__(
        self,
        rules_path: Path | None = None,
        statuses: tuple[str, ...] = ("verified",),
        rules: list[dict] | None = None,
    ) -> None:
        if rules is None:
            path = rules_path or CANON_DIR / "schools" / "qiongtong.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            rules = data.get("rules", [])
        self.statuses = statuses
        self.canggan = load_canggan()
        self.index: dict[tuple[str, str], dict] = {}
        for rule in rules:
            if rule.get("rule_status", "candidate") not in statuses:
                continue
            conds = dict(_parse_condition(c) for c in rule["if"])
            key = (conds.get("日干", ""), conds.get("月支", ""))
            self.index[key] = rule

    @property
    def rule_count(self) -> int:
        return len(self.index)

    def _rule_stems(self, rule: dict) -> list[str]:
        """校勘核定的用神序列优先；candidate 冒烟时退回脚本候选。"""
        if rule.get("verified_stems"):
            return list(rule["verified_stems"])
        for clause in rule.get("then", []):
            if isinstance(clause, dict) and "调候用神候选" in clause:
                return list(clause["调候用神候选"])
        return []

    def features(self, chart: dict) -> dict | None:
        """chart 需含 year/month/day/time_pillar 与 day_master。

        返回 None 表示无适用规则（该格未校勘通过或排盘缺月柱）。
        时柱缺失（三柱人群）时透干统计只覆盖年月两干，n_visible_stems 记录口径。
        """
        day_master = chart.get("day_master") or ""
        month_pillar = chart.get("month_pillar") or ""
        if not day_master or len(month_pillar) < 2:
            return None
        month_zhi = month_pillar[1]
        rule = self.index.get((day_master, month_zhi))
        if rule is None:
            return None
        stems = self._rule_stems(rule)
        if not stems:
            return None

        visible = [
            pillar[0]
            for key in ("year_pillar", "month_pillar", "time_pillar")
            if (pillar := chart.get(key)) and len(pillar) >= 1
        ]
        hidden = [
            gan
            for key in ("year_pillar", "month_pillar", "day_pillar", "time_pillar")
            if (pillar := chart.get(key)) and len(pillar) >= 2
            for gan in self.canggan.get(pillar[1], [])
        ]
        primary = stems[0]
        return {
            "qt_rule_id": rule["rule_id"],
            "qt_rule_status": rule.get("rule_status", "candidate"),
            "qt_stems": "".join(stems),
            "qt_primary": primary,
            "qt_primary_tou": int(primary in visible),
            "qt_primary_cang": int(primary in hidden),
            "qt_tou_count": sum(s in visible for s in stems),
            "qt_cang_count": sum(s in hidden for s in stems),
            "qt_n_stems": len(stems),
            "n_visible_stems": len(visible),
        }
