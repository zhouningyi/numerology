"""阅读页译文筛选：丢掉网页垃圾、伪繁简转换，优先真白话。

华严经曾被「句级对齐」层整章盖住：坏的现代释译压过了正确的 aligned 白话。
这里在挂接前做质量门与择优。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

try:
    from zhconv import convert as zh_convert
except ImportError:  # 无 zhconv 时退化为恒等
    def zh_convert(text: str, _target: str) -> str:  # type: ignore[misc]
        return text


# 网页导航/装饰噪音（对齐误把卷首 HTML 文案挂上）
JUNK_MARKERS = (
    "[详情]",
    "放大字体",
    "白话华严经",
    "【原典】",
    "作者：洪启嵩",
    "[投稿]",
    "关闭",
    "缩小",
    "入门",
    "讲解",
    "问答",
    "文章",
    "原文\n译文",
    "华严经是大乘佛教修学",
)

# 真白话常见词；几乎没有则疑似只做了繁简转换
_MODERN_MARKERS = (
    "这部", "当时", "这时", "他的", "他们", "已经", "正在", "时候",
    "十分", "非常", "这样", "那个", "就是", "进行", "成就了", "安坐",
    "所谓的", "以及", "而且", "因为", "所以", "如果", "但是", "然后",
    "自己", "一切的", "无上", "正等正觉", "如实", "宣说",
)


def _han_only(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fff]", "", text or "")


def contains_web_junk(text: str) -> bool:
    if not text:
        return False
    if any(marker in text for marker in JUNK_MARKERS):
        return True
    # 卷首导航块：短句密布换行 + 站名
    if text.count("\n") >= 8 and ("华严经" in text[:80] or "洪启嵩" in text):
        return True
    return False


def is_simplified_only(original: str, translation: str) -> bool:
    """译文是否几乎只是原文的繁体→简体（不算真正白话）。"""
    if not original or not translation:
        return False
    if contains_web_junk(translation):
        return False  # 垃圾另论
    o = _han_only(zh_convert(original, "zh-cn"))
    t = _han_only(zh_convert(translation, "zh-cn"))
    if len(o) < 4 or len(t) < 4:
        return False
    ratio = SequenceMatcher(None, o, t).ratio()
    has_modern = any(marker in translation for marker in _MODERN_MARKERS)
    # 高重叠且无现代叙述词 → 伪译文
    if ratio >= 0.90 and not has_modern:
        return True
    # 极短句：如「如是我闻」→「如是我闻」
    if len(o) <= 12 and ratio >= 0.95 and not has_modern:
        return True
    return False


def clean_pair_text(text: str) -> str:
    """去掉对里混入的网页装饰行，保留正文。"""
    if not text:
        return ""
    if not contains_web_junk(text):
        return text.strip()
    lines = []
    skip_prefixes = (
        "[详情]", "放大", "缩小", "关闭", "作者：", "[投稿]", "【原典】",
        "入门", "讲解", "问答", "文章", "原文", "译文", "华严经是大乘",
    )
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(s.startswith(p) or s == p for p in skip_prefixes):
            continue
        if s in {"华严经", "白话华严经 第一卷", "[华严经]", "正常"}:
            continue
        if re.fullmatch(r"第[一二三四五六七八九十百零〇\d]+卷", s):
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def sanitize_translation_row(row: dict, original_text: str | None = None) -> dict | None:
    """清洗一条译文；完全不可用则返回 None。"""
    item = dict(row)
    text = item.get("text") or ""
    pairs = item.get("pairs")
    if pairs:
        cleaned_pairs = []
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            orig, trans = pair[0], clean_pair_text(pair[1] or "")
            if not trans or contains_web_junk(trans):
                continue
            if original_text and is_simplified_only(orig or "", trans):
                continue
            cleaned_pairs.append([orig, trans])
        if cleaned_pairs:
            item["pairs"] = cleaned_pairs
            item["text"] = "\n".join(p[1] for p in cleaned_pairs if p[1])
            text = item["text"]
        else:
            # pairs 全废，尝试清洗合并 text
            text = clean_pair_text(text)
            item["text"] = text
            item.pop("pairs", None)
    else:
        text = clean_pair_text(text)
        item["text"] = text

    if not (item.get("text") or "").strip():
        return None
    if contains_web_junk(item["text"]):
        return None
    if original_text and is_simplified_only(original_text, item["text"]):
        return None
    # 坏对齐不得再标 high
    if item.get("confidence") == "high" and (
        "句级对齐" in str(item.get("alignment_method") or "")
        or item.get("translation_source") == "洪启嵩译（模型仅对齐）"
    ):
        item["confidence"] = "low"
        item["review_status"] = item.get("review_status") or "candidate"
        item["alignment_status"] = "候选（已清洗，待复核）"
    return item


def score_translation(row: dict, original_text: str | None = None) -> float:
    """越高越好；不可用为极大负分。"""
    if row is None:
        return -1e9
    text = row.get("text") or ""
    if contains_web_junk(text):
        return -1000
    if original_text and is_simplified_only(original_text, text):
        return -500
    score = 0.0
    if row.get("review_status") == "human_verified":
        score += 100
    elif row.get("review_status") == "model_agree":
        score += 30
    elif row.get("review_status") == "rejected":
        score -= 200
    # 真白话源优先
    source = str(row.get("translation_source") or "")
    method = str(row.get("alignment_method") or "")
    if "洪启嵩" in source and "模型仅对齐" not in source:
        score += 40
    if row.get("layer") == "现代白话" and ("对齐" in method or "洪启嵩" in source):
        score += 35
    if "句级对齐提取" in method and "补译占比 100%" in method:
        score -= 20  # 几乎全补译，质量不稳
    if "项目生成" in source or "一原文单元" in method:
        score += 25
    # 强制句对齐产物优先于旧的「模型仅对齐」漂移层
    if row.get("prompt_version") == "force-align-v1" or "强制句对齐" in source:
        score += 55
    if "模型仅对齐" in source and "强制" not in source:
        score -= 30
    if row.get("pairs") and len(row.get("pairs") or []) >= 1:
        score += 10
    if any(m in text for m in _MODERN_MARKERS):
        score += 15
    # 略惩罚纯繁简嫌疑的中等重叠
    if original_text:
        o = _han_only(zh_convert(original_text, "zh-cn"))
        t = _han_only(text)
        if o and t:
            ratio = SequenceMatcher(None, o, t).ratio()
            if ratio > 0.85:
                score -= 25
    return score


def select_inline_translations(
    original_segments: list[dict],
    candidates: list[dict],
) -> list[dict]:
    """为每个原文段选最优译文；丢掉垃圾与伪繁简。"""
    originals_by_index = {
        s.get("segment_index"): s
        for s in original_segments
        if s.get("segment_index") is not None
    }
    buckets: dict[Any, list[dict]] = {}
    passthrough: list[dict] = []  # 无段号的整章译文

    for raw in candidates:
        targets = raw.get("original_segment_indices") or []
        oi = raw.get("original_segment_index")
        if oi is None and targets:
            oi = targets[0]
        original_text = None
        if oi is not None and oi in originals_by_index:
            original_text = originals_by_index[oi].get("text")
        cleaned = sanitize_translation_row(raw, original_text)
        if cleaned is None:
            continue
        if oi is None and not targets:
            passthrough.append(cleaned)
            continue
        # 多目标：挂到每个目标各自评估（通常是连续拆分）
        indices = targets if targets else [oi]
        for index in indices:
            if index is None:
                continue
            buckets.setdefault(index, []).append(cleaned)

    selected: list[dict] = []
    used_ids: set[int] = set()
    for oi, options in buckets.items():
        original_text = (originals_by_index.get(oi) or {}).get("text")
        good = [
            row for row in options
            if score_translation(row, original_text) >= 0
        ]
        if not good:
            continue
        # 长原文常被拆成多条真白话：保留分数接近最优的全部，按出现顺序
        best_score = max(score_translation(row, original_text) for row in good)
        keep = [
            row for row in good
            if score_translation(row, original_text) >= best_score - 20
        ]
        keep.sort(key=lambda row: (
            row.get("source_paragraph_index") is None,
            row.get("source_paragraph_index") or 0,
            row.get("segment_index") if isinstance(row.get("segment_index"), int) else 0,
        ))
        seen_text: set[str] = set()
        for row in keep:
            text = (row.get("text") or "").strip()
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            item = dict(row)
            item["original_segment_index"] = oi
            item["original_segment_indices"] = [oi]
            selected.append(item)
            used_ids.add(id(row))

    # 无段号的整章译文保留，供 unmatched 区域
    for row in passthrough:
        if id(row) not in used_ids:
            selected.append(row)
    return selected
