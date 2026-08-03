#!/usr/bin/env python3
"""规范化语料层状态：降级非法 high、补 provenance、规范化周易 section_key。

默认只处理华严 generated 与周易 layers；可 --dry-run 只看 diff。
NDE 重分类见 reclassify_nde.py。
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from numerology.corpus_quality import (
    attach_normalized_section_keys,
    normalize_generated_huayan_rows,
    normalize_section_key,
)

LAYERS = Path("data/processed/canon/layers")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """原子写回，避免并发读到半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    return bak


def normalize_huayan_generated(dry_run: bool) -> dict:
    path = LAYERS / "huayan_t0279_generated_layers.jsonl"
    if not path.exists():
        return {"path": str(path), "exists": False}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fixed = normalize_generated_huayan_rows(rows)
    illegal_before = sum(1 for r in rows if r.get("confidence") == "high")
    illegal_after = sum(1 for r in fixed if r.get("confidence") == "high")
    if not dry_run:
        _backup(path)
        _write_jsonl(path, fixed)
    return {
        "path": str(path),
        "rows": len(rows),
        "high_before": illegal_before,
        "high_after": illegal_after,
        "written": not dry_run,
    }


def normalize_huayan_aligned(dry_run: bool) -> dict:
    path = LAYERS / "huayan_t0279_aligned_layers.jsonl"
    if not path.exists() or path.stat().st_size == 0:
        return {"path": str(path), "exists": False}
    from numerology.corpus_quality import apply_quality_fields

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fixed = [
        apply_quality_fields(row, pipeline="align_canon_models")
        for row in rows
    ]
    if not dry_run:
        _backup(path)
        _write_jsonl(path, fixed)
    return {
        "path": str(path),
        "rows": len(fixed),
        "high_after": sum(1 for r in fixed if r.get("confidence") == "high"),
        "written": not dry_run,
    }


def normalize_yijing(dry_run: bool) -> dict:
    path = LAYERS / "yijing_layers.jsonl"
    if not path.exists():
        return {"path": str(path), "exists": False}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fixed = []
    changed = 0
    for row in rows:
        item = dict(row)
        raw = item.get("section_key")
        new = normalize_section_key(raw)
        if raw != new:
            changed += 1
            item["section_key_raw"] = raw
            item["section_key"] = new
        else:
            item["section_key"] = new
        fixed.append(item)
    # 也跑一遍 attach 保证一致
    fixed = attach_normalized_section_keys(fixed)
    if not dry_run:
        _backup(path)
        _write_jsonl(path, fixed)
    return {
        "path": str(path),
        "rows": len(fixed),
        "keys_changed": changed,
        "written": not dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--only",
        choices=["huayan_generated", "huayan_aligned", "yijing", "all"],
        default="all",
    )
    args = parser.parse_args()
    results = {}
    if args.only in {"huayan_generated", "all"}:
        results["huayan_generated"] = normalize_huayan_generated(args.dry_run)
    if args.only in {"huayan_aligned", "all"}:
        results["huayan_aligned"] = normalize_huayan_aligned(args.dry_run)
    if args.only in {"yijing", "all"}:
        results["yijing"] = normalize_yijing(args.dry_run)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
