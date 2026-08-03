#!/usr/bin/env python3
"""列出古籍译文人工复核队列（默认华严 candidate）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from numerology.corpus_quality import apply_quality_fields
from numerology.corpus_review import apply_reviews_to_rows, build_review_queue, unit_key_from_row

LAYERS = Path("data/processed/canon/layers")


def load_book_rows(book: str) -> tuple[list[dict], list[dict]]:
    originals_path = LAYERS / f"{book}_layers.jsonl"
    originals = []
    if originals_path.exists():
        originals = [
            json.loads(line)
            for line in originals_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        originals = [r for r in originals if r.get("layer") == "原文"]

    rows: list[dict] = []
    for name, pipeline in (
        (f"{book}_aligned_layers.jsonl", "align_canon_models"),
        (f"{book}_modern_layers.jsonl", "process_huayan_modern"),
        (f"{book}_generated_layers.jsonl", "translate_huayan_segments"),
        (f"{book}_layers.jsonl", "process_canon_layers"),
    ):
        path = LAYERS / name
        if not path.exists() or path.stat().st_size == 0:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("layer") not in {"现代白话", "现代释译"}:
                continue
            if name.endswith("_layers.jsonl") and book == "huayan_t0279" and "generated" not in name and "aligned" not in name and "modern" not in name:
                # 华严原文层跳过
                continue
            rows.append(apply_quality_fields(row, pipeline=pipeline))
    rows = apply_reviews_to_rows(book, rows)
    for row in rows:
        row["unit_key"] = unit_key_from_row(book, row)
    return rows, originals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default="huayan_t0279")
    parser.add_argument(
        "--status",
        default="candidate",
        help="candidate|model_agree|human_verified|rejected|all",
    )
    parser.add_argument("--chapter", type=int)
    parser.add_argument("--volume", type=int)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    status = None if args.status in {"", "all"} else args.status
    rows, originals = load_book_rows(args.book)
    queue = build_review_queue(
        args.book,
        rows,
        originals=originals,
        status=status,
        chapter=args.chapter,
        volume=args.volume,
        limit=args.limit,
        offset=args.offset,
    )
    if args.as_json:
        # 控制体积：json 模式去掉全文
        slim = dict(queue)
        slim["items"] = [
            {k: v for k, v in item.items() if k not in {"original_text", "translation_text"}}
            for item in queue["items"]
        ]
        print(json.dumps(slim, ensure_ascii=False, indent=2))
        return
    print(
        f"{queue['book']} status={queue['status_filter']} "
        f"total={queue['total']} showing={len(queue['items'])} "
        f"counts={queue['status_counts']}"
    )
    for item in queue["items"]:
        print(
            f"- ch={item.get('chapter')} vol={item.get('volume')} "
            f"O{item.get('original_segment_index')} · {item.get('review_status')}"
        )
        print(f"  原文: {item['original_preview'][:80].replace(chr(10), ' ')}")
        print(f"  译文: {item['translation_preview'][:80].replace(chr(10), ' ')}")
        print(f"  打开: {item['url']}")


if __name__ == "__main__":
    main()
