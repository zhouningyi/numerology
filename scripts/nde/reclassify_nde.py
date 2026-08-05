#!/usr/bin/env python3
"""用当前规则重分类已有 experiences.jsonl（不重下 HTML）。

默认刷新 categories（问卷现象）；可加 --motifs 同时刷新正文母题。
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

from numerology.nde.parser import classify, load_motifs, load_phenomena, tag_motifs

EXPERIENCES = Path("data/processed/nderf/experiences.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--motifs", action="store_true",
        help="同时按 motifs.yaml 重标叙述正文母题",
    )
    parser.add_argument(
        "--categories-only", action="store_true",
        help="只刷问卷现象（默认行为；与 --motifs 可并用）",
    )
    args = parser.parse_args()

    if not EXPERIENCES.exists():
        raise SystemExit(f"缺少 {EXPERIENCES}")

    phenomena = load_phenomena()
    motifs = load_motifs() if args.motifs else {}
    rows = [
        json.loads(line)
        for line in EXPERIENCES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        rows = rows[: args.limit]

    old_counts = Counter(cat for r in rows for cat in (r.get("categories") or {}))
    old_motifs = Counter(m for r in rows for m in (r.get("motifs") or {}))
    changed = 0
    motif_changed = 0
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
        if args.motifs:
            new_motifs = tag_motifs(row.get("description") or "", motifs)
            if set(new_motifs) != set(row.get("motifs") or {}):
                motif_changed += 1
            row["motifs"] = new_motifs

    new_counts = Counter(cat for r in rows for cat in (r.get("categories") or {}))
    new_motif_counts = Counter(m for r in rows for m in (r.get("motifs") or {}))
    summary = {
        "total": len(rows),
        "changed_cases": changed,
        "motif_changed_cases": motif_changed if args.motifs else None,
        "old_counts": dict(old_counts),
        "new_counts": dict(new_counts),
        "delta": {
            key: new_counts.get(key, 0) - old_counts.get(key, 0)
            for key in sorted(set(old_counts) | set(new_counts))
            if new_counts.get(key, 0) != old_counts.get(key, 0)
        },
        "motif_counts": dict(new_motif_counts) if args.motifs else dict(old_motifs),
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

