#!/usr/bin/env python3
"""把 NDERF 原始页面快照解析为结构化案例库。

data/raw/nderf/pages/pages_*.jsonl → data/processed/nderf/experiences.jsonl
可在抓取进行中随时运行（只解析已抓到的部分），重复运行覆盖输出。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from numerology.nde.parser import load_phenomena, parse_experience

PAGES_DIR = Path("data/raw/nderf/pages")
OUTPUT = Path("data/processed/nderf/experiences.jsonl")


def main() -> None:
    records = []
    failed = 0
    for shard in sorted(PAGES_DIR.glob("pages_*.jsonl")):
        with shard.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed = parse_experience(row["url"], row["html"])
                if parsed is None:
                    failed += 1
                    continue
                parsed["fetched_at"] = row.get("fetched_at")
                records.append(parsed)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as handle:
        for record in sorted(records, key=lambda r: r["slug"]):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    phenomena = load_phenomena()
    counts = Counter(cat for r in records for cat in r["categories"])
    print(f"解析 {len(records)} 篇（失败 {failed}）-> {OUTPUT}")
    for key, spec in phenomena.items():
        print(f"  {spec['name']}: {counts.get(key, 0)}")


if __name__ == "__main__":
    main()
