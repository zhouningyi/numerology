#!/usr/bin/env python3
"""把可按卦名对应的《东坡易传》聚合到《周易》章节。

《易传》的系辞、说卦、序卦、杂卦没有稳定的单卦边界，另存为全书相关来源，
不复制到 64 卦中制造虚假的对应关系。
"""

from __future__ import annotations

import json
import re
from pathlib import Path


LAYERS = Path("data/processed/canon/layers")
YIJING = LAYERS / "yijing_layers.jsonl"
DONGPO = LAYERS / "dongpo_yizhuan_layers.jsonl"
RELATED = LAYERS / "yijing_related_layers.jsonl"
GENERAL = LAYERS / "yijing_related_sources.json"


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def yijing_hexagram_name(title: str) -> str | None:
    match = re.search(r"第\S+?卦\s+([^\s]+)", title or "")
    return normalize(match.group(1)) if match else None


def dongpo_hexagram_name(title: str) -> str | None:
    match = re.search(r"：\s*(.*?)\s*卦", title or "")
    return normalize(match.group(1)) if match else None


def process() -> dict:
    yijing = [json.loads(line) for line in YIJING.read_text(encoding="utf-8").splitlines()]
    dongpo = [json.loads(line) for line in DONGPO.read_text(encoding="utf-8").splitlines()]
    chapter_by_name = {}
    title_by_chapter = {}
    for row in yijing:
        chapter = row.get("chapter")
        if not chapter or chapter > 64:
            continue
        name = yijing_hexagram_name(row.get("chapter_title", ""))
        if name:
            chapter_by_name[name] = chapter
            title_by_chapter[chapter] = row.get("chapter_title")

    related = []
    unmatched = []
    for row in dongpo:
        if not row.get("chapter"):
            continue
        name = dongpo_hexagram_name(row.get("chapter_title", ""))
        target = chapter_by_name.get(name)
        if target is None:
            unmatched.append({"source_chapter": row.get("chapter"), "source_title": row.get("chapter_title")})
            continue
        item = dict(row)
        item.update({
            "book": "yijing",
            "chapter": target,
            "chapter_title": title_by_chapter[target],
            "layer": "相关著作",
            "source_book": "dongpo_yizhuan",
            "source_title": "东坡易传",
            "source_chapter": row.get("chapter"),
            "source_layer": row.get("layer"),
            "marker": f"东坡易传 · {row.get('layer')}",
            "confidence": "high",
            "alignment_status": "按卦名聚合",
            "alignment_method": "《周易》卦名 ↔《东坡易传》卦名",
        })
        related.append(item)

    RELATED.parent.mkdir(parents=True, exist_ok=True)
    with RELATED.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(related):
            row["related_segment_index"] = index
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    general = {
        "book": "yijing",
        "sources": [{
            "source_book": "dongpo_yizhuan",
            "title": "东坡易传",
            "mapping": "按卦名挂接到《周易》各卦的“相关著作”区域",
            "chapters": list(range(1, 65)),
            "url": "/canon/dongpo_yizhuan",
        }, {
            "source_book": "yizhuan",
            "title": "易传",
            "mapping": "全书通论，未强行归入单一卦",
            "chapters": [1, 2, 3, 4, 5],
            "url": "/canon/yizhuan",
        }],
        "unmatched_dongpo": unmatched,
    }
    GENERAL.write_text(json.dumps(general, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "related_records": len(related),
        "mapped_hexagrams": len({row["chapter"] for row in related}),
        "unmatched_dongpo_records": len(unmatched),
        "general_sources": len(general["sources"]),
    }


if __name__ == "__main__":
    print(json.dumps(process(), ensure_ascii=False))
