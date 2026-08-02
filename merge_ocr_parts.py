#!/usr/bin/env python3
"""按 PDF 页码合并可续跑 OCR 分片，并在覆盖完整后替换主 JSONL。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def merge(source: Path, part: Path, output: Path, total_pages: int, text_output: Path | None = None) -> None:
    rows = []
    for path in (source, part):
        with path.open(encoding="utf-8") as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    by_page = {}
    for row in rows:
        page = int(row["page_pdf"])
        if page in by_page:
            raise ValueError(f"发现重复 PDF 页码：{page}")
        by_page[page] = row
    expected = set(range(1, total_pages + 1))
    actual = set(by_page)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"页码覆盖不完整；缺失={missing[:10]}，多出={extra[:10]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for page in sorted(by_page):
            handle.write(json.dumps(by_page[page], ensure_ascii=False) + "\n")
    if text_output:
        with text_output.open("w", encoding="utf-8") as handle:
            for page in sorted(by_page):
                handle.write(f"\n\n===== PDF 第 {page} 页 =====\n{by_page[page].get('text_raw', '')}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--part", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--total-pages", type=int, required=True)
    parser.add_argument("--text-output", type=Path)
    args = parser.parse_args()
    merge(args.source, args.part, args.output, args.total_pages, args.text_output)
    print(f"合并完成：{args.output}（{args.total_pages} 页，无重复/缺页）")


if __name__ == "__main__":
    main()
