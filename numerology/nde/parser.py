"""NDERF 案例页解析：原始 HTML → 结构化记录 + 现象分类。

每篇案例含叙述正文（Experience Description）和标准化问卷（问题→答案）。
现象分类基于问卷答案判定（见 phenomena.yaml），每个命中类别都保留证据答案，
可回查到原文；不做对叙述文本的模糊猜测。
"""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

import yaml

NDE_DIR = Path(__file__).parent
PHENOMENA_PATH = NDE_DIR / "phenomena.yaml"

_TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S)
# 词边界否定：no 后须是结束或标点/空白，且排除 "no longer"
_NEGATIVE_RE = re.compile(
    r"^(?:"
    r"no(?! longer)(?=$|[\s,.:;!\-])|"
    r"uncertain(?=$|[\s,.:;!\-])|"
    r"none(?=$|[\s,.:;!\-])|"
    r"n/?a(?=$|[\s,.:;!\-])|"
    r"don't know|do not know|"
    r"i did not|i didn't|"
    r"unknown(?=$|[\s,.:;!\-])"
    r")",
    re.I,
)


def load_phenomena() -> dict:
    return yaml.safe_load(PHENOMENA_PATH.read_text(encoding="utf-8"))["categories"]


def html_to_lines(raw_html: str) -> list[str]:
    text = _TAG_RE.sub("", raw_html)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_experience(url: str, raw_html: str) -> dict | None:
    """解析单篇案例；页面结构异常时返回 None（保留 raw 供人工检查）。"""
    lines = html_to_lines(raw_html)
    slug = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".htm").removesuffix(".html")

    title = next((l for l in lines if "| NDERF" in l), slug).replace("| NDERF", "").strip()
    classification = None
    for i, line in enumerate(lines):
        if line == "Classification" and i + 1 < len(lines):
            classification = lines[i + 1]
            break
        if line.startswith("Classification "):
            classification = line.removeprefix("Classification ").strip()
            break

    # 新版页面为 "Experience Description"，早年版本是 "Experience Description :"
    desc_start = None
    for i, line in enumerate(lines):
        if line.rstrip(" :").strip() == "Experience Description":
            desc_start = i + 1
            break
    if desc_start is None:
        return None
    desc_end = desc_start
    while desc_end < len(lines) and not lines[desc_end].startswith("Background Information"):
        desc_end += 1
    description = "\n".join(lines[desc_start:desc_end])

    # 问卷：问题行以 ? 结尾或以 : 结尾的字段标签；其后到下一问题为答案
    qa: list[dict] = []
    question: str | None = None
    answer: list[str] = []
    for line in lines[desc_end:]:
        is_question = line.endswith("?") or (line.endswith(":") and len(line) < 90)
        if is_question:
            if question is not None:
                qa.append({"q": question.rstrip(":?"), "a": "\n".join(answer).strip()})
            question = line
            answer = []
        elif question is not None:
            answer.append(line)
    if question is not None:
        qa.append({"q": question.rstrip(":?"), "a": "\n".join(answer).strip()})

    return {
        "slug": slug,
        "url": url,
        "title": title,
        "classification": classification,
        "description": description,
        "qa": qa,
        "categories": classify(qa),
    }


def _is_negative_answer(answer: str) -> bool:
    """整句是否以否定应答起头（词边界，避免 no longer 误杀）。"""
    normalized = answer.strip().lower()
    if not normalized:
        return True
    return bool(_NEGATIVE_RE.match(normalized))


def _is_positive(answer: str, positive_contains: list[str] | None) -> bool:
    """阳性判定：否定应答优先；有 positive_contains 时再要求命中关键词。"""
    normalized = answer.strip().lower()
    if not normalized:
        return False
    # 否定应答始终优先，避免 “No, I did not acquire gifts” 因 acquire 误判
    if _is_negative_answer(normalized):
        return False
    if positive_contains:
        return any(term.lower() in normalized for term in positive_contains)
    return True


def classify(qa: list[dict], phenomena: dict | None = None) -> dict[str, str]:
    """问卷 → {类别: 证据答案}。只记录阳性类别。"""
    phenomena = phenomena or load_phenomena()
    result: dict[str, str] = {}
    for key, spec in phenomena.items():
        for rule in spec["match"]:
            needle = rule["question_contains"].lower()
            for pair in qa:
                if needle in pair["q"].lower() and _is_positive(
                    pair["a"], rule.get("positive_contains")
                ):
                    # 取答案首行作证据摘要
                    result[key] = pair["a"].split("\n")[0][:200]
                    break
            if key in result:
                break
    return result
