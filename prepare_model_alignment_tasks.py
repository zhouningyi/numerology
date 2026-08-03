#!/usr/bin/env python3
"""生成古籍现代译文的模型对齐任务，不直接调用模型。

输出是可审计的 JSONL：原文、译文、来源、允许的一对多/多对一关系和约束都保留。
模型只能提交候选映射；只有人工复核后才回填 original_segment_index。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


LAYERS = Path("data/processed/canon/layers")
OUT = Path("data/processed/canon/alignment_tasks")


def read_rows(name: str) -> list[dict]:
    path = LAYERS / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def base_task(task_id: str, book: str, volume: int | None, chapter: int, title: str | None) -> dict:
    return {
        "task_id": task_id,
        "book": book,
        "volume": volume,
        "chapter": chapter,
        "chapter_title": title,
        "status": "pending",
        "alignment_rules": [
            "保持原文与译文顺序单调，不允许交叉对应",
            "允许多个译文段落对应一个原文段落",
            "允许一个译文段落覆盖多个原文段落",
            "无法确认时返回 unmatched，不得猜测",
            "输出 confidence、evidence 和 boundary_note",
        ],
    }


def huayan_tasks() -> list[dict]:
    originals = read_rows("huayan_t0279_layers.jsonl")
    modern = read_rows("huayan_t0279_modern_layers.jsonl")
    original_groups = defaultdict(list)
    for row in originals:
        original_groups[(row["volume"], row["chapter"])].append(row)
    tasks = []
    for row in modern:
        key = (row["volume"], row["chapter"])
        task = base_task(
            f"huayan:{row['volume']:02d}:{row['chapter']:02d}:{row['segment_index']:04d}",
            "huayan_t0279", row["volume"], row["chapter"], row.get("chapter_title"),
        )
        task.update({
            "source_file": row["source_file"],
            "source_url": row["source_url"],
            "original_segments": [
                {"segment_index": item["segment_index"], "text": item["text"]}
                for item in original_groups[key]
            ],
            "modern_paragraphs": [
                {"paragraph_index": index, "text": text}
                for index, text in enumerate(row.get("source_paragraphs", []))
            ],
        })
        tasks.append(task)
    return tasks


def dongpo_tasks() -> list[dict]:
    rows = read_rows("dongpo_yizhuan_layers.jsonl")
    grouped = defaultdict(dict)
    for row in rows:
        if row.get("chapter"):
            grouped[row["chapter"]][row["layer"]] = row
    tasks = []
    for chapter, parts in sorted(grouped.items()):
        original = parts.get("原文")
        modern = parts.get("现代白话")
        if not original or not modern:
            continue
        task = base_task(
            f"dongpo_yizhuan:{chapter:02d}", "dongpo_yizhuan", None,
            chapter, original.get("chapter_title"),
        )
        task["alignment_mode"] = "semantic_or_chapter"
        task["note"] = "现代白话是苏轼解读摘要，不是逐字翻译；优先按卦辞、彖象、爻位和文言语义挂接，无法确认则保留本卦级。"
        task["original_segments"] = [{
            "segment_index": original["segment_index"],
            "text": original["text"],
        }]
        task["modern_paragraphs"] = [
            {"paragraph_index": index, "text": text}
            for index, text in enumerate(modern["text"].splitlines()) if text.strip()
        ]
        tasks.append(task)
    return tasks


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tasks = huayan_tasks() + dongpo_tasks()
    path = OUT / "huayan_and_dongpo.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")
    print(json.dumps({"path": str(path), "tasks": len(tasks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
