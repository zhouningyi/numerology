#!/usr/bin/env python3
"""为生平事实抽取生成可复现的 ADB 分层样本。"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from numerology.db.schema import init_db


SCHEMA_VERSION = "biography-extraction-v1"


def _rodden_group(rating: str | None) -> str:
    if rating in {"AA", "A", "B", "C"}:
        return rating
    if rating in {"DD", "X", "XX", "AX", "AAX", "DX"}:
        return "DD_X_XX"
    return "unknown"


def _era(year: int | None) -> str:
    if year is None:
        return "unknown"
    if year < 1900:
        return "before_1900"
    if year < 1950:
        return "1900_1949"
    if year < 2000:
        return "1950_1999"
    return "2000_plus"


def _strata(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _rodden_group(row["rodden_rating"]),
        row["gender"] or "unknown",
        _era(row["birth_year"]),
        "with_events" if row["has_events"] else "without_events",
    )


def _allocate(group_sizes: dict[tuple[str, ...], int], size: int) -> dict[tuple[str, ...], int]:
    """按组规模分配样本数，同时尽量保证小组至少抽到一条。"""
    total = sum(group_sizes.values())
    if size >= total:
        return dict(group_sizes)
    keys = sorted(group_sizes)
    targets = {
        key: max(1, int(size * count / total)) if size >= len(keys) else 0
        for key, count in group_sizes.items()
    }
    while sum(targets.values()) > size:
        candidates = [key for key in keys if targets[key] > 1]
        key = max(candidates, key=lambda item: targets[item])
        targets[key] -= 1
    while sum(targets.values()) < size:
        candidates = [key for key in keys if targets[key] < group_sizes[key]]
        key = max(candidates, key=lambda item: (group_sizes[item] - targets[item], item))
        targets[key] += 1
    return targets


def sample_biographies(
    db_path: Path,
    output: Path,
    blind_output: Path,
    size: int = 300,
    seed: int = 20260731,
) -> dict[str, Any]:
    """生成本地抽取版与线上盲检版，返回样本清单。"""
    if size <= 0:
        raise ValueError("size 必须为正数")
    conn = init_db(db_path)
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """SELECT p.id AS person_id, p.source_id, p.name, p.gender,
                          p.birth_year, p.birth_date, p.birth_time,
                          p.rodden_rating, p.biography,
                          CASE WHEN e.person_id IS NULL THEN 0 ELSE 1 END AS has_events
                   FROM persons p
                   JOIN person_quality_profiles q ON q.person_id = p.id
                   LEFT JOIN (SELECT DISTINCT person_id FROM events_normalized) e
                     ON e.person_id = p.id
                   WHERE p.source = 'adb'
                     AND q.analysis_tier = 'full_bazi'
                     AND p.biography IS NOT NULL
                     AND TRIM(p.biography) != ''
                   ORDER BY p.id"""
            ).fetchall()
        ]
    finally:
        conn.close()

    if size > len(rows):
        raise ValueError(f"请求 {size} 条，但可用样本只有 {len(rows)} 条")

    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        row["strata"] = _strata(row)
        groups[row["strata"]].append(row)
    targets = _allocate({key: len(value) for key, value in groups.items()}, size)

    selected = []
    for index, key in enumerate(sorted(groups)):
        rng = random.Random(seed + index)
        candidates = list(groups[key])
        rng.shuffle(candidates)
        selected.extend(candidates[: targets[key]])
    random.Random(seed).shuffle(selected)

    run_id = f"bio-{seed}-{size}"
    local_lines = []
    blind_lines = []
    strata_counts: dict[str, int] = defaultdict(int)
    for index, row in enumerate(selected, start=1):
        task_id = f"{run_id}-{index:04d}"
        strata = {
            "rodden_group": row["strata"][0],
            "gender": row["strata"][1],
            "birth_era": row["strata"][2],
            "event_coverage": row["strata"][3],
        }
        strata_counts["|".join(row["strata"])] += 1
        local_lines.append(
            json.dumps(
                {
                    "task_id": task_id,
                    "schema_version": SCHEMA_VERSION,
                    "person_id": row["person_id"],
                    "source_id": row["source_id"],
                    "name": row["name"],
                    "birth_date": row["birth_date"],
                    "birth_time": row["birth_time"],
                    "rodden_rating": row["rodden_rating"],
                    "strata": strata,
                    "text": row["biography"],
                    "status": "pending",
                },
                ensure_ascii=False,
            )
        )
        blind_lines.append(
            json.dumps(
                {
                    "task_id": task_id,
                    "schema_version": SCHEMA_VERSION,
                    "text": row["biography"],
                },
                ensure_ascii=False,
            )
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    blind_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(local_lines) + "\n", encoding="utf-8")
    blind_output.write_text("\n".join(blind_lines) + "\n", encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "requested_size": size,
        "actual_size": len(selected),
        "source": "adb",
        "analysis_tier": "full_bazi",
        "strata_counts": dict(sorted(strata_counts.items())),
        "local_output": str(output),
        "blind_output": str(blind_output),
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="生成传记抽取分层样本")
    parser.add_argument("--db", type=Path, default=Path("data/numerology.db"))
    parser.add_argument("--size", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--blind-output", type=Path, default=None)
    args = parser.parse_args()
    suffix = f"{args.seed}_n{args.size}"
    output = args.output or Path(f"data/tasks/biography_sample_{suffix}.jsonl")
    blind_output = args.blind_output or Path(f"data/tasks/biography_blind_{suffix}.jsonl")
    manifest = sample_biographies(args.db, output, blind_output, args.size, args.seed)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
