#!/usr/bin/env python3
"""解析本地《白话华严经》快照，并建立原文—现代译文的保守段落对齐层。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup


BOOK = "huayan_t0279"
RAW_ROOT = Path("data/raw/canon/huayan_t0279/modern_hrfjw")
LAYER_PATH = Path("data/processed/canon/layers/huayan_t0279_modern_layers.jsonl")
ORIGINAL_PATH = Path("data/processed/canon/layers/huayan_t0279_layers.jsonl")
HEADING_RE = re.compile(
    r"卷第(?P<volume>[〇零一二三四五六七八九十百]+)[：:]\s*"
    r"(?P<title>.+?品)(?:第)?(?P<chapter>[〇零一二三四五六七八九十百]+)"
    r"(?:之(?P<sub>[〇零一二三四五六七八九十百]+))?【白话】"
)


def chinese_number(value: str) -> int:
    digits = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
              "百": 100}
    if value.isdigit():
        return int(value)
    total = 0
    current = 0
    for char in value:
        number = digits[char]
        if number in {10, 100}:
            total += (current or 1) * number
            current = 0
        else:
            current = current * 10 + number
    return total + current


def clean_text(value: str) -> str:
    # 网页用空格排版中文，保存为阅读文本时去掉这些版式空白。
    return re.sub(r"\s+", "", value or "").strip()


def parse_page(path: Path, volume: int, source_url: str) -> list[dict]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    rows = []
    for heading in soup.select("div.B1_text h2"):
        title = " ".join(heading.get_text(" ", strip=True).split())
        match = HEADING_RE.search(title)
        if not match:
            continue
        paragraphs = []
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) == "h2":
                break
            if getattr(sibling, "name", None) != "p":
                continue
            text = clean_text(sibling.get_text(" ", strip=True))
            if text:
                paragraphs.append(text)
        rows.append({
            "book": BOOK,
            "layer": "现代白话",
            "confidence": "high",
            "marker": None,
            "translation_source": "洪启嵩译",
            "volume": volume,
            "chapter": chinese_number(match.group("chapter")),
            "chapter_title_source": match.group("title"),
            "book_chapter_label_source": title.replace("【白话】", ""),
            "source_file": path.name,
            "source_url": source_url,
            "paragraphs": paragraphs,
        })
    return rows


def proportional_groups(paragraphs: list[str], originals: list[dict]) -> list[list[str]]:
    """按段落累计长度做单调分组，不把它当作人工校勘结论。"""
    if not originals:
        return []
    groups = [[] for _ in originals]
    if not paragraphs:
        return groups
    original_lengths = [max(1, len(row["text"])) for row in originals]
    modern_lengths = [max(1, len(text)) for text in paragraphs]
    original_total = sum(original_lengths)
    modern_total = sum(modern_lengths)
    original_cumulative = []
    cursor = 0
    for length in original_lengths:
        cursor += length
        original_cumulative.append(cursor)
    modern_cursor = 0
    for text, length in zip(paragraphs, modern_lengths):
        midpoint = modern_cursor + length / 2
        target = midpoint / modern_total * original_total
        index = next((i for i, end in enumerate(original_cumulative) if target <= end), len(originals) - 1)
        groups[index].append(text)
        modern_cursor += length
    return groups


def process() -> dict:
    from numerology.corpus_quality import REVIEW_CANDIDATE, STATUS_LABELS, build_provenance

    originals = [json.loads(line) for line in ORIGINAL_PATH.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads((RAW_ROOT / "manifest.json").read_text(encoding="utf-8"))
    source_urls = {int(row["volume"]): row["url"] for row in manifest["volumes"]}
    all_source_rows = []
    for path in sorted(RAW_ROOT.glob("volume-*.html")):
        volume = int(path.stem.rsplit("-", 1)[1])
        all_source_rows.extend(parse_page(path, volume, source_urls[volume]))

    records = []
    for source in all_source_rows:
        candidates = [
            row for row in originals
            if row["volume"] == source["volume"] and row["chapter"] == source["chapter"]
        ]
        if not candidates:
            continue
        # 这里故意只保留卷/品级译文。累计字符长度无法判断语义边界，
        # 不再把它伪装成原文逐段对应；模型任务另行生成，审核通过后再回填
        # original_segment_index。
        original = candidates[0]
        records.append({
            "book": BOOK,
            "chapter": original["chapter"],
            "chapter_title": original["chapter_title"],
            "book_chapter_label": original["book_chapter_label"],
            "volume": original["volume"],
            "source_file": source["source_file"],
            "source_url": source["source_url"],
            "layer": "现代白话",
            "confidence": "low",
            "review_status": REVIEW_CANDIDATE,
            "marker": source["marker"],
            "translation_source": source.get("translation_source"),
            "text": "\n\n".join(source["paragraphs"]),
            "segment_index": len(records),
            "source_paragraphs": source["paragraphs"],
            "source_paragraph_count": len(source["paragraphs"]),
            "alignment_method": "按卷/品保留网页译文，未强行逐段配对",
            "alignment_status": STATUS_LABELS[REVIEW_CANDIDATE],
            "provenance": build_provenance(
                pipeline="process_huayan_modern",
                source=source.get("translation_source"),
                extra={"granularity": "volume_chapter", "source_file": source["source_file"]},
            ),
        })

    LAYER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LAYER_PATH.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    missing = [
        {"volume": volume, "reason": "页面未找到【白话】正文区块"}
        for volume in range(1, 81)
        if not any(row["volume"] == volume for row in all_source_rows)
    ]
    summary = {
        "source_sections": len(all_source_rows),
        "source_paragraphs": sum(len(row["paragraphs"]) for row in all_source_rows),
        "source_records": len(records),
        "aligned_records": 0,
        "alignment_status": "pending_model_alignment",
        "missing_volumes": missing,
    }
    (RAW_ROOT / "processing_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(process(), ensure_ascii=False))
