#!/usr/bin/env python3
"""物化古籍规则特征表：SQLite（bazi）→ data/features/<school>.parquet。

默认只用 verified 规则。--allow-candidate 仅供流水线冒烟：
输出文件强制加 _candidate_preview 后缀，禁止进入正式分析。
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from numerology.canon.engine import QiongtongEngine

DB_PATH = Path("data/numerology.db")
FEATURES_DIR = Path("data/features")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--school", default="qiongtong", choices=["qiongtong"])
    parser.add_argument(
        "--allow-candidate", action="store_true",
        help="允许未校勘规则（仅冒烟；输出文件加 _candidate_preview 后缀）",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    statuses = ("verified", "candidate") if args.allow_candidate else ("verified",)
    engine = QiongtongEngine(statuses=statuses)
    if engine.rule_count == 0:
        raise SystemExit(
            "没有可用规则：verified 为 0。先在 /canon/rules/qiongtong 完成校勘，"
            "或用 --allow-candidate 冒烟。"
        )

    suffix = "_candidate_preview" if args.allow_candidate else ""
    output = FEATURES_DIR / f"{args.school}{suffix}.parquet"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sql = """SELECT b.person_id, b.year_pillar, b.month_pillar, b.day_pillar,
                    b.time_pillar, b.day_master, b.has_time_pillar,
                    p.rodden_rating, p.source
             FROM bazi b JOIN persons p ON p.id = b.person_id"""
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"

    rows_out: list[dict] = []
    total = matched = 0
    for row in conn.execute(sql):
        total += 1
        feats = engine.features(dict(row))
        if feats is None:
            continue
        matched += 1
        rows_out.append({
            "person_id": row["person_id"],
            "source": row["source"],
            "rodden_rating": row["rodden_rating"],
            "has_time_pillar": row["has_time_pillar"],
            **feats,
        })
    conn.close()

    if not rows_out:
        raise SystemExit("没有产生任何特征行。")

    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows_out)
    table = table.replace_schema_metadata({
        "school": args.school,
        "rule_statuses": ",".join(statuses),
        "rule_count": str(engine.rule_count),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "build_canon_features.py",
    })
    pq.write_table(table, output)

    tou_rate = sum(r["qt_primary_tou"] for r in rows_out) / len(rows_out)
    cang_rate = sum(r["qt_primary_cang"] for r in rows_out) / len(rows_out)
    print(f"命盘 {total} 条，命中规则 {matched} 条 -> {output}")
    print(f"规则数 {engine.rule_count}（状态：{','.join(statuses)}）")
    print(f"主用神透干率 {tou_rate:.3f}，主用神藏支率 {cang_rate:.3f}")
    if args.allow_candidate:
        print("⚠ 本文件含未校勘规则，仅供流水线冒烟，不得用于正式分析。")


if __name__ == "__main__":
    main()
