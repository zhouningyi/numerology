#!/usr/bin/env python3
"""华严经句句对齐：强制 DP（主）+ 可选本地 Ollama 裁决。

示例：
  python3 -m scripts.canon.align_huayan_sentences --chapter 1 --materialize
  python3 -m scripts.canon.align_huayan_sentences --chapter 1 --ollama --model qwen3:8b --materialize

方案说明（不要先盲训模型）：
  1. 有段级 aligned 白话时，在「原文段 × 该段白话」内做句级 DP（最准）；
  2. 否则在章参考白话上按游标窗口 DP；
  3. 低分句可选用本地 Ollama 在候选窗内选句；
  4. 将来若 fine-tune，应用本脚本产出的 pairs 做 LoRA 金标。
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from numerology.huayan_sentence_align import align_chapter_segments
from scripts.canon.translate_canon_segments import load_references

LAYERS = Path("data/processed/canon/layers")
BOOK = "huayan_t0279"
LAYER_PATH = LAYERS / f"{BOOK}_generated_layers.jsonl"
# 稳定底稿：合并时若当前层过薄则回退
SNAP_PATHS = [
    LAYERS / f"{BOOK}_generated_layers.normalized.snap",
    LAYERS / f"{BOOK}_generated_layers.v2.bak",
    LAYERS / f"{BOOK}_generated_layers.jsonl.bak",
]


def load_originals(chapter: int | None) -> list[dict]:
    rows = []
    with (LAYERS / f"{BOOK}_layers.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("layer") != "原文":
                continue
            if chapter is not None and int(row.get("chapter") or -1) != chapter:
                continue
            rows.append(row)
    rows.sort(key=lambda r: (r.get("volume") or 0, r.get("segment_index") or 0))
    return rows


def _row_key(row: dict) -> tuple:
    oi = row.get("original_segment_index")
    if oi is None and row.get("original_segment_indices"):
        oi = row["original_segment_indices"][0]
    if oi is None:
        oi = row.get("segment_index")
    try:
        oi = int(oi)
    except (TypeError, ValueError):
        oi = str(oi)
    unit = row.get("translation_unit_index", 0)
    if row.get("prompt_version") == "force-align-v1":
        unit = 0
    return (oi, unit)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def materialize_merge(new_rows: list[dict]) -> dict:
    """合并写入：保留其他章旧层，仅用 force-align 覆盖同原文段。"""
    base_rows = _load_jsonl(LAYER_PATH)
    if len(base_rows) < 500:
        # 当前层疑似被截断，从快照恢复底稿
        for snap in SNAP_PATHS:
            snap_rows = _load_jsonl(snap)
            if len(snap_rows) > len(base_rows):
                base_rows = snap_rows
                break

    if LAYER_PATH.exists() and LAYER_PATH.stat().st_size:
        shutil.copy2(LAYER_PATH, LAYER_PATH.with_suffix(".jsonl.bak_before_force_align"))

    existing: dict[tuple, dict] = {}
    for row in base_rows:
        existing[_row_key(row)] = row

    # 删除将被 force-align 覆盖的旧段（同 original 的所有 unit）
    replace_ois = set()
    for row in new_rows:
        oi = row.get("original_segment_index")
        try:
            replace_ois.add(int(oi))
        except (TypeError, ValueError):
            replace_ois.add(oi)
    existing = {
        key: row for key, row in existing.items()
        if key[0] not in replace_ois
    }
    for row in new_rows:
        existing[_row_key(row)] = row

    final = list(existing.values())
    final.sort(key=lambda r: (
        r.get("volume") if isinstance(r.get("volume"), int) else 0,
        r.get("chapter") if isinstance(r.get("chapter"), int) else 0,
        r.get("original_segment_index") if isinstance(r.get("original_segment_index"), int) else 0,
        r.get("translation_unit_index") or 0,
    ))
    tmp = LAYER_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in final:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(LAYER_PATH)
    return {
        "path": str(LAYER_PATH),
        "total": len(final),
        "upserted": len(new_rows),
        "base_rows": len(base_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--materialize", action="store_true")
    parser.add_argument("--ollama", action="store_true", help="低分句用本地 Ollama 重判")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--limit-segments", type=int, default=None)
    args = parser.parse_args()

    originals = load_originals(args.chapter)
    if args.limit_segments:
        originals = originals[: args.limit_segments]
    refs = load_references(BOOK)
    ref_text = refs.get(str(args.chapter), "")
    if not ref_text:
        raise SystemExit(f"章 {args.chapter} 无参考白话（modern_layers）")

    def progress(msg: str) -> None:
        print(msg, flush=True)

    rows = align_chapter_segments(
        originals,
        ref_text,
        use_ollama=args.ollama,
        model=args.model,
        progress=progress,
        book=BOOK,
    )
    total_pairs = sum(len(r.get("pairs") or []) for r in rows)
    filled = sum(1 for r in rows for p in (r.get("pairs") or []) if p[1])
    low = sum(1 for r in rows for sc in (r.get("pair_scores") or []) if sc < 0.35)
    summary = {
        "chapter": args.chapter,
        "segments": len(rows),
        "pairs": total_pairs,
        "filled_pairs": filled,
        "fill_rate": round(filled / max(1, total_pairs), 4),
        "low_score_pairs": low,
        "sample": [
            {
                "O": r["original_segment_index"],
                "method": r.get("alignment_method", "")[:40],
                "n": len(r.get("pairs") or []),
                "first": (r.get("pairs") or [["", ""]])[0],
            }
            for r in rows[:6]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.materialize:
        info = materialize_merge(rows)
        print(json.dumps({"materialized": info}, ensure_ascii=False))
    else:
        out = Path(f"data/processed/canon/alignment_candidates/force_align_ch{args.chapter}.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps({"wrote_candidates": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
