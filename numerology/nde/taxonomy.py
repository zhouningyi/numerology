"""标签体系归纳（taxonomy induction）的纯函数层。

自由抽取的短语无法直接计数：同一现象散落在几十种表述里（实测"被牵引"
1674 篇命中、18 种说法、最高频的一种只占 53%）。本模块提供把自由输出
变成可计数标签的三件事：批次规划（map-reduce 归并）、标注信度、
体系体检（频次上下界与共现冗余）。

所有函数不调用 API，可单测；编排与模型调用在 scripts/nde/induce_taxonomy.py。
"""

from __future__ import annotations

import re
from collections import Counter

# 段落切分：太短的段落缺乏语境，抽取质量差；太长会让模型漏掉细节
MIN_PARAGRAPH_CHARS = 120
MAX_PARAGRAPH_CHARS = 1500

# 体系体检阈值（来自本项目实测教训：no_judgment 曾误标 3819/5671 篇）
FREQ_TOO_BROAD = 0.60   # 命中率高于此 → 概念太宽泛，应拆分
FREQ_TOO_RARE = 0.005   # 低于此 → 太细碎，合并或降级为变体
JACCARD_REDUNDANT = 0.80  # 两标签共现相似度高于此 → 实为同一现象


def split_paragraphs(
    text: str,
    min_chars: int = MIN_PARAGRAPH_CHARS,
    max_chars: int = MAX_PARAGRAPH_CHARS,
) -> list[str]:
    """按空行切段，过短的并入前段，过长的按句切开。

    抽取单元取段落而非整篇：整篇会让模型只报最显著的两三个现象，
    罕见现象永远浮不上来。
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    merged: list[str] = []
    for block in blocks:
        if merged and len(merged[-1]) < min_chars:
            merged[-1] = merged[-1] + " " + block
        else:
            merged.append(block)
    out: list[str] = []
    for block in merged:
        while len(block) > max_chars:
            cut = block.rfind(". ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            out.append(block[: cut + 1].strip())
            block = block[cut + 1 :].strip()
        if block:
            out.append(block)
    return [b for b in out if len(b) >= 40]


def batched(items: list, size: int) -> list[list]:
    if size <= 0:
        raise ValueError("批大小必须为正")
    return [items[i : i + size] for i in range(0, len(items), size)]


def plan_merge_rounds(
    n_items: int,
    batch_size: int = 200,
    compress_ratio: int = 10,
    target: int = 120,
    max_rounds: int = 6,
) -> list[dict]:
    """规划分层归并：每轮把 batch_size 项压成 batch_size/compress_ratio 项。

    单次调用塞不下几万条短语，所以分批压缩、逐层收敛（map-reduce）。
    返回每轮的 (输入项数, 批数, 预计输出项数)，供成本预估与进度显示。
    """
    plan = []
    current = n_items
    for _ in range(max_rounds):
        if current <= target or current <= batch_size // compress_ratio:
            break
        n_batches = max(1, -(-current // batch_size))  # 向上取整
        expected = max(target, n_batches * max(1, batch_size // compress_ratio))
        plan.append({"input": current, "batches": n_batches, "expected": expected})
        if expected >= current:  # 压不动了，防止死循环
            break
        current = expected
    return plan


def normalize_phrase(phrase: str) -> str:
    """归一化短语用于精确去重：小写、去冠词与人称、压空白。

    只做保守归一（不做词干还原），语义级归并交给模型。
    """
    text = (phrase or "").strip().lower()
    text = re.sub(r"\b(a|an|the|my|his|her|their|our|its)\b", " ", text)
    text = re.sub(r"[^\w\s一-鿿]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def dedupe_phrases(phrases: list[str]) -> list[tuple[str, int]]:
    """精确去重并计频，按频次降序。频次进归并 prompt，防高频现象被合并掉。"""
    counter: Counter = Counter()
    display: dict[str, str] = {}
    for phrase in phrases:
        key = normalize_phrase(phrase)
        if not key:
            continue
        counter[key] += 1
        display.setdefault(key, phrase.strip())
    return [(display[key], count) for key, count in counter.most_common()]


def jaccard(left: set, right: set) -> float:
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def krippendorff_alpha_binary(pairs: list[tuple[int, int]]) -> float | None:
    """二元名义数据、两名标注者的 Krippendorff's α。

    pairs 是每个单元的两次标注 (0/1, 0/1)。α<0.67 的标签不可用于统计，
    0.67–0.80 只能做探索性描述，>0.80 才可进入正式分析。
    单元全为同一类别（无变异）时 α 未定义，返回 None。
    """
    if not pairs:
        return None
    o_disagree = 0
    n_ones = n_zeros = 0
    for first, second in pairs:
        first, second = int(bool(first)), int(bool(second))
        n_ones += first + second
        n_zeros += (1 - first) + (1 - second)
        if first != second:
            o_disagree += 2  # (0,1) 与 (1,0) 各计一次
    total = n_ones + n_zeros
    if total < 2 or n_ones == 0 or n_zeros == 0:
        return None
    expected = 2 * n_ones * n_zeros / (total - 1)
    return 1.0 - o_disagree / expected


def agreement_report(
    first: dict[str, set], second: dict[str, set], labels: list[str]
) -> dict:
    """两轮标注的逐标签信度：α + 一致率 + 两轮各自命中数。

    first/second 是 {文档 id: 命中标签集合}。
    """
    docs = sorted(set(first) & set(second))
    report = {}
    for label in labels:
        pairs = [
            (int(label in first[doc]), int(label in second[doc])) for doc in docs
        ]
        agree = sum(1 for a, b in pairs if a == b)
        report[label] = {
            "alpha": krippendorff_alpha_binary(pairs),
            "agreement": agree / len(pairs) if pairs else None,
            "hits_first": sum(a for a, _ in pairs),
            "hits_second": sum(b for _, b in pairs),
            "n": len(pairs),
        }
    return report


def audit_taxonomy(
    doc_labels: dict[str, set], total_docs: int | None = None
) -> dict:
    """体系体检：过宽/过窄标签与高共现冗余对。

    doc_labels 是 {文档 id: 命中标签集合}。
    """
    total = total_docs or len(doc_labels)
    counts: Counter = Counter()
    by_label: dict[str, set] = {}
    for doc, labels in doc_labels.items():
        for label in labels:
            counts[label] += 1
            by_label.setdefault(label, set()).add(doc)
    too_broad = [l for l, c in counts.items() if total and c / total > FREQ_TOO_BROAD]
    too_rare = [l for l, c in counts.items() if total and c / total < FREQ_TOO_RARE]
    redundant = []
    names = sorted(by_label)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            score = jaccard(by_label[left], by_label[right])
            if score >= JACCARD_REDUNDANT:
                redundant.append({"pair": [left, right], "jaccard": round(score, 3)})
    uncovered = [doc for doc, labels in doc_labels.items() if not labels]
    return {
        "total_docs": total,
        "labels": len(counts),
        "counts": dict(counts.most_common()),
        "coverage": 1 - len(uncovered) / total if total else 0.0,
        "uncovered": uncovered,
        "too_broad": sorted(too_broad, key=lambda l: -counts[l]),
        "too_rare": sorted(too_rare, key=lambda l: counts[l]),
        "redundant_pairs": sorted(redundant, key=lambda r: -r["jaccard"]),
    }


def select_residual(
    doc_labels: dict[str, set], per_doc_units: dict[str, list[str]] | None = None
) -> list[str]:
    """残差集：一个标签都没命中的文档，用于下一轮开放抽取。

    残差迭代是罕见现象浮现的唯一途径——首轮抽取会被"光/隧道"这类
    高频现象淹没，只有把已覆盖的排除掉，长尾才有机会被看见。
    """
    residual = [doc for doc, labels in doc_labels.items() if not labels]
    if per_doc_units:
        residual = [doc for doc in residual if per_doc_units.get(doc)]
    return sorted(residual)
