#!/usr/bin/env python3
"""从穷通宝鉴分层语料抽取调候用神候选规则（T1 查表型流派）。

产出 numerology/canon/schools/qiongtong.yaml：每个（月支 × 日干）一条规则，
带原文引文锚定。全部规则为 rule_status: candidate —— 用神候选由脚本从
条文首句解析，仅供人工校勘起点；未经人工核对不得进入预注册
（实施计划 P2.5：规则表必须以原文为准，模型/脚本产物只是候选）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

LAYERS_PATH = Path("data/processed/canon/layers/qiongtong_baojian_layers.jsonl")
OUTPUT_PATH = Path("numerology/canon/schools/qiongtong.yaml")
MONTH_TABLE = Path("numerology/canon/tables/month_zhi.yaml")

STEMS = "甲乙丙丁戊己庚辛壬癸"
# 月份 token 解析顺序：长 token 优先，避免“十一”被拆成“十”“一”
MONTH_TOKENS = ["十一", "十二", "正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]
MONTH_ALIAS = {"冬": "十一", "腊": "十二"}


def parse_title(title: str) -> tuple[list[str], str] | None:
    """章节标题 → （月份列表, 日干）；总论等无月份章节返回 None。

    兼容“五、六月甲木”“正二月戊土”“十十一十二月己土”等合并写法。
    """
    m = re.match(r"^(.+?)月([" + STEMS + r"])[木火土金水]$", title.replace("、", ""))
    if not m:
        return None
    month_part, stem = m.group(1), m.group(2)
    months: list[str] = []
    i = 0
    while i < len(month_part):
        for token in MONTH_TOKENS:
            if month_part.startswith(token, i):
                months.append(MONTH_ALIAS.get(token, token))
                i += len(token)
                break
        else:
            return None  # 出现无法识别的字符，交人工处理
    normalized = [m if m in ("十一", "十二") else m for m in months]
    return normalized, stem


def candidate_stems(first_line: str, day_stem: str) -> list[str]:
    """从条文首句解析被提及的天干（排除日干本身），作为调候用神候选。

    只是文本共现，不是结论；例如“得丙癸逢，富贵双全”→ [丙, 癸]。
    """
    seen: list[str] = []
    for ch in first_line:
        if ch in STEMS and ch != day_stem and ch not in seen:
            seen.append(ch)
    return seen


# 注意：十天干在 Unicode 中不连续，不能写 [甲-癸] 区间
_S = f"[{STEMS}]"
_HINT_PATTERNS = [
    re.compile(rf"先[用取]?{_S}[木火土金水]?[，,]?后[用取]?{_S}[木火土金水]?"),
    re.compile(rf"{_S}[木火土金水]?次之"),
    re.compile(rf"专用{_S}[木火土金水]?"),
    re.compile(rf"{_S}[木火土金水]?为(?:主|先|尊|辅|佐|用)"),
]


def priority_hints(first_line: str) -> list[str]:
    """从条文首句抓取用神先后次序的原文短语，供人工校勘定序。"""
    hints: list[str] = []
    for pattern in _HINT_PATTERNS:
        for match in pattern.findall(first_line):
            if match not in hints:
                hints.append(match)
    return hints


# 人工校勘写回的字段：重跑抽取时必须保留，不得被脚本覆盖
REVIEW_FIELDS = ("rule_status", "verified_stems", "verified_order", "review_note", "reviewed_at")


def merge_review_fields(new_rules: list[dict], existing_path: Path) -> None:
    """把已有 YAML 中的人工校勘字段合并回新生成的规则。"""
    if not existing_path.exists():
        return
    existing = yaml.safe_load(existing_path.read_text(encoding="utf-8")) or {}
    by_id = {r["rule_id"]: r for r in existing.get("rules", [])}
    for rule in new_rules:
        old = by_id.get(rule["rule_id"])
        if not old:
            continue
        for field in REVIEW_FIELDS:
            if field in old and old.get(field) not in (None, "", []):
                rule[field] = old[field]


def first_canon_line(segments: list[dict]) -> str | None:
    """取章节第一条原文条文（跳过“徐乐吾曰”评注行）。"""
    for seg in segments:
        if seg["layer"] != "原文":
            continue
        for line in seg["text"].splitlines():
            line = line.strip()
            if not line or line.startswith("徐乐吾曰"):
                continue
            return line
    return None


def main() -> None:
    month_zhi = yaml.safe_load(MONTH_TABLE.read_text(encoding="utf-8"))["months"]
    with LAYERS_PATH.open(encoding="utf-8") as handle:
        segments = [json.loads(line) for line in handle]

    by_chapter: dict[int, list[dict]] = {}
    titles: dict[int, str] = {}
    for seg in segments:
        if seg["chapter"] is None:
            continue
        by_chapter.setdefault(seg["chapter"], []).append(seg)
        if seg.get("chapter_title"):
            titles.setdefault(seg["chapter"], seg["chapter_title"])

    rules = []
    covered: set[tuple[str, str]] = set()
    skipped: list[str] = []
    for chapter in sorted(by_chapter):
        title = titles.get(chapter, "")
        parsed = parse_title(title)
        if parsed is None:
            skipped.append(f"第{chapter}章 {title}")
            continue
        months, day_stem = parsed
        quote = first_canon_line(by_chapter[chapter])
        if not quote:
            skipped.append(f"第{chapter}章 {title}（无原文条文）")
            continue
        stems = candidate_stems(quote, day_stem)
        for month in months:
            zhi = month_zhi[f"{month}月"]
            if (zhi, day_stem) in covered:
                continue
            covered.add((zhi, day_stem))
            rules.append({
                "rule_id": f"qiongtong_{day_stem}_{zhi}",
                "school": "qiongtong",
                "source_book": "穷通宝鉴",
                "edition": "徐乐吾评注网页录入本（luckclub），待扫描本核对",
                "source_section": title,
                "quote": quote,
                "text_layer": "原文",
                "if": [f"日干 == {day_stem}", f"月支 == {zhi}"],
                "then": [{"调候用神候选": stems}],
                "priority_hints": priority_hints(quote),
                "rule_type": "feature",
                "candidate_y": [],
                "operational": True,
                "rule_status": "candidate",
                "needs_human_review": True,
            })

    merge_review_fields(rules, OUTPUT_PATH)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 穷通宝鉴 调候用神候选表（脚本从语料抽取，逐条带原文引文）\n"
        "# 所有规则 rule_status: candidate：候选用神来自条文首句的天干共现解析，\n"
        "# 必须人工比对原文与扫描本后改为 verified，才可用于特征生成与预注册。\n"
        f"# 生成脚本：extract_qiongtong_rules.py；覆盖 {len(covered)}/120 个（月支×日干）单元\n"
    )
    body = yaml.safe_dump(
        {"school": "qiongtong", "rules": rules},
        allow_unicode=True, sort_keys=False, width=120,
    )
    OUTPUT_PATH.write_text(header + body, encoding="utf-8")
    print(f"生成 {len(rules)} 条规则（覆盖 {len(covered)}/120 单元）-> {OUTPUT_PATH}")
    missing = [
        (zhi, stem)
        for stem in STEMS
        for zhi in "寅卯辰巳午未申酉戌亥子丑"
        if (zhi, stem) not in covered
    ]
    if missing:
        print(f"缺失单元 {len(missing)} 个: {missing}")
    if skipped:
        print("跳过章节：", "；".join(skipped))


if __name__ == "__main__":
    main()
