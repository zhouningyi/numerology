#!/usr/bin/env python3
"""从《道德经》王弼本 wikitext 提取八十一章原文，明确排除王弼注文。"""

from __future__ import annotations

import json
import re
from pathlib import Path


BOOK = "daodejing_wangbi"
RAW_PATH = Path(f"data/raw/canon/{BOOK}/wikisource/daodejing_wangbi.wikitext")
PROCESSED_ROOT = Path("data/processed/canon")
LAYERS_ROOT = PROCESSED_ROOT / "layers"
CHAPTER_RE = re.compile(r"^==\s*([一二三四五六七八九十百〇零]+)章\s*==$")
CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100}


def chinese_number(value: str) -> int:
    """解析一至八十一的中文章号。"""
    total = current = 0
    for char in value:
        number = CHINESE_DIGITS[char]
        if number in {10, 100}:
            total += (current or 1) * number
            current = 0
        else:
            current = current * 10 + number
    return total + current


def clean_original_line(line: str) -> str:
    """仅保留正文行：维基页中的冒号行是王弼注，模板/分类也不属于正文。"""
    line = line.strip()
    if not line or line.startswith((":", "{{", "[[", "|", "<!--", "=")):
        return ""
    line = re.sub(r"<[^>]+>", "", line)
    line = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", line)
    line = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line)
    line = re.sub(r"''+", "", line).strip()
    return line if not any(token in line for token in ("老子道德經注", "晉　王弼注", "華亭張氏原本")) else ""


def parse_chapters(wikitext: str) -> list[dict]:
    chapters: list[dict] = []
    current_number: int | None = None
    lines: list[str] = []

    def flush() -> None:
        if current_number is None:
            return
        text = "\n".join(lines).strip()
        if text:
            chapters.append({"chapter": current_number, "text": text})

    for raw in wikitext.splitlines():
        match = CHAPTER_RE.match(raw.strip())
        if match:
            flush()
            current_number = chinese_number(match.group(1))
            lines = []
            continue
        # 第八十一章之后是维基页附录、跋语与词条，不属于《道德经》正文。
        # 遇到任意同级标题即结束当前经文，不能把后续资料并进末章。
        if current_number is not None and raw.strip().startswith("="):
            flush()
            current_number = None
            lines = []
            continue
        if current_number is not None:
            cleaned = clean_original_line(raw)
            if cleaned:
                lines.append(cleaned)
    flush()
    return chapters


def process() -> tuple[Path, Path, dict]:
    chapters = parse_chapters(RAW_PATH.read_text(encoding="utf-8"))
    numbers = [item["chapter"] for item in chapters]
    if numbers != list(range(1, 82)):
        raise ValueError(f"王弼本应有连续八十一章，实际章号：{numbers}")
    records = []
    for index, item in enumerate(chapters):
        chapter = item["chapter"]
        records.append({
            "book": BOOK,
            "chapter": chapter,
            "chapter_title": f"第{chapter}章",
            "book_chapter_label": f"王弼本 · 第{chapter}章",
            "volume": 1 if chapter <= 37 else 2,
            "source_file": RAW_PATH.name,
            "layer": "原文",
            "confidence": "high",
            "marker": "王弼本正文（王弼注未并入）",
            "segment_index": index,
            "text": item["text"],
        })
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    LAYERS_ROOT.mkdir(parents=True, exist_ok=True)
    online = PROCESSED_ROOT / f"{BOOK}_online.txt"
    online.write_text("\n\n".join(item["text"] for item in records), encoding="utf-8")
    layers = LAYERS_ROOT / f"{BOOK}_layers.jsonl"
    with layers.open("w", encoding="utf-8") as handle:
        for item in records:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return online, layers, {"chapters": len(records), "segments": len(records), "commentary_excluded": True}


if __name__ == "__main__":
    online_path, layer_path, stats = process()
    print(json.dumps({"online": str(online_path), "layers": str(layer_path), **stats}, ensure_ascii=False))
