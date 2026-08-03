#!/usr/bin/env python3
"""用当前 phenomena.yaml 规则重分类已有 experiences.jsonl（不重下 HTML）。

保留叙述、问卷与元数据，只刷新 categories；写出 diff 摘要。
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

from numerology.nde.parser import classify, load_phenomena

EXPERIENCES = Path("data/processed/nderf/experiences.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not EXPERIENCES.exists():
        raise SystemExit(f"缺少 {EXPERIENCES}")

    phenomena = load_phenomena()
    rows = [
        json.loads(line)
        for line in EXPERIENCES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        rows = rows[: args.limit]

    old_counts = Counter(cat for r in rows for cat in (r.get("categories") or {}))
    changed = 0
    samples = []
    for row in rows:
        fresh = classify(row.get("qa") or [], phenomena)
        old = row.get("categories") or {}
        if set(old) != set(fresh) or any(old.get(k) != fresh.get(k) for k in fresh):
            changed += 1
            if len(samples) < 12:
                samples.append({
                    "slug": row.get("slug"),
                    "added": sorted(set(fresh) - set(old)),
                    "removed": sorted(set(old) - set(fresh)),
                })
        row["categories"] = fresh

    new_counts = Counter(cat for r in rows for cat in (r.get("categories") or {}))
    summary = {
        "total": len(rows),
        "changed_cases": changed,
        "old_counts": dict(old_counts),
        "new_counts": dict(new_counts),
        "delta": {
            key: new_counts.get(key, 0) - old_counts.get(key, 0)
            for key in sorted(set(old_counts) | set(new_counts))
        },
        "samples": samples,
        "written": False,
    }

    if not args.dry_run:
        bak = EXPERIENCES.with_suffix(".jsonl.bak")
        shutil.copy2(EXPERIENCES, bak)
        with EXPERIENCES.open("w", encoding="utf-8") as handle:
            for row in sorted(rows, key=lambda r: r.get("slug") or ""):
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        summary["written"] = True
        summary["backup"] = str(bak)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
