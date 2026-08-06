#!/usr/bin/env python3
"""将 CBETA T0279 原文拆为卷、品和段落，接入古籍阅读页。"""

from __future__ import annotations

import json
import re
from pathlib import Path


BOOK = "huayan_t0279"
RAW_ROOT = Path("data/raw/canon/huayan_t0279/cbeta/cbeta_text/cbeta_text")
PROCESSED_ROOT = Path("data/processed/canon")
LAYERS_ROOT = PROCESSED_ROOT / "layers"

CHINESE_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100}
SECTION_RE = re.compile(r"^(.+品)(?:第)?([〇零一二三四五六七八九十百]+)(?:之([〇零一二三四五六七八九十百]+))?$")
VOLUME_RE = re.compile(r"^大方廣佛華嚴經卷第([〇零一二三四五六七八九十百]+)$")


def chinese_number(value: str) -> int:
    """解析古籍标题中的中文数字，覆盖本书使用的 1–80。"""
    if value.isdigit():
        return int(value)
    total = 0
    current = 0
    for char in value:
        number = CHINESE_DIGITS[char]
        if number in {10, 100}:
            total += (current or 1) * number
            current = 0
        else:
            current = current * 10 + number
    return total + current


def section_heading(line: str) -> tuple[int, str, str] | None:
    match = SECTION_RE.match(line.strip())
    if not match:
        return None
    title = match.group(1)
    number = chinese_number(match.group(2))
    sub = chinese_number(match.group(3)) if match.group(3) else None
    label = f"{title}第{number}" + (f"之{sub}" if sub is not None else "")
    return number, title, label


def clean_paragraph(lines: list[str]) -> str:
    return "\n".join(line.strip() for line in lines if line.strip())


def process() -> tuple[Path, Path, dict]:
    records: list[dict] = []
    current_chapter = 0
    current_title = "大周新译序"
    current_label = "序"
    current_volume = 0

    for path in sorted(RAW_ROOT.glob("T0279_[0-9][0-9][0-9].txt")):
        volume = int(path.stem.rsplit("_", 1)[1])
        current_volume = volume
        lines = path.read_text(encoding="utf-8").splitlines()
        paragraph: list[str] = []
        in_text = False

        def flush() -> None:
            text = clean_paragraph(paragraph)
            if not text:
                return
            records.append({
                "book": BOOK,
                "chapter": current_chapter,
                "chapter_title": current_title,
                "book_chapter_label": f"卷第{current_volume:02d} · {current_label}",
                "volume": current_volume,
                "source_file": path.name,
                "layer": "原文",
                "confidence": "low",
                "marker": None,
                "text": text,
            })

        for raw in lines:
            line = raw.strip()
            if line.startswith("#") or not line:
                flush()
                paragraph.clear()
                continue
            if line.startswith("No. 279"):
                in_text = True
                continue
            if not in_text and VOLUME_RE.match(line):
                in_text = True
            if not in_text:
                continue
            volume_match = VOLUME_RE.match(line)
            if volume_match:
                flush()
                paragraph.clear()
                current_volume = chinese_number(volume_match.group(1))
                continue
            if line in {"于闐國三藏實叉難陀奉　制譯", "大方廣佛華嚴經卷第一"} or line.startswith("大方廣佛華嚴經卷第"):
                flush()
                paragraph.clear()
                continue
            heading = section_heading(line)
            if heading:
                flush()
                paragraph.clear()
                current_chapter, current_title, current_label = heading
                continue
            paragraph.append(line)
        flush()

    for index, record in enumerate(records):
        record["segment_index"] = index

    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    LAYERS_ROOT.mkdir(parents=True, exist_ok=True)
    online_path = PROCESSED_ROOT / f"{BOOK}_online.txt"
    online_path.write_text("\n\n".join(record["text"] for record in records), encoding="utf-8")
    layer_path = LAYERS_ROOT / f"{BOOK}_layers.jsonl"
    with layer_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    stats = {
        "segments": len(records),
        "chapters": len({record["chapter"] for record in records}),
        "volumes": len({record["volume"] for record in records}),
        "sections": len({(record["chapter"], record["chapter_title"]) for record in records}),
    }
    return online_path, layer_path, stats


if __name__ == "__main__":
    online, layers, stats = process()
    print(json.dumps({"online": str(online), "layers": str(layers), **stats}, ensure_ascii=False))
