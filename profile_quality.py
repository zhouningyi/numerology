#!/usr/bin/env python3
"""生成跨来源统一的出生资料分析层级。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from numerology.db.schema import init_db


RULE_VERSION = "2026-07-31"


def _date_quality(row: sqlite3.Row) -> str:
    """根据来源原生精度判断出生日期精度。"""
    if row["birth_year"] is None:
        return "unknown"
    if row["date_precision"] == 11:
        return "day"
    if row["date_precision"] == 10:
        return "month"
    if row["date_precision"] == 9:
        return "year"
    if row["birth_date"] and len(row["birth_date"]) == 10:
        return "day"
    if row["birth_month"]:
        return "month"
    return "year"


def _time_quality(row: sqlite3.Row) -> str:
    """判断是否有分钟级出生时刻。"""
    if row["birth_hour"] is not None and row["birth_minute"] is not None:
        return "minute"
    return "unknown"


def _analysis_tier(date_quality: str, time_quality: str) -> str:
    """将日期/时间精度转换成跨来源分析资格层级。"""
    if date_quality == "day" and time_quality == "minute":
        return "full_bazi"
    if date_quality == "day":
        return "three_pillars"
    if date_quality in {"month", "year"}:
        return "date_interval"
    return "unusable"


def _quality_flags(row: sqlite3.Row, date_quality: str, time_quality: str) -> list[str]:
    flags = []
    if row["entry_type"] != "person":
        flags.append("non_person_entry")
    if row["birth_year"] is None:
        flags.append("birth_year_missing")
    if date_quality != "day":
        flags.append(f"birth_date_{date_quality}")
    if time_quality == "unknown":
        flags.append("birth_time_missing")
    if row["source"] == "adb":
        if row["rodden_rating"] is None:
            flags.append("rodden_missing")
        elif row["rodden_rating"] in {"C", "DD", "X", "XX", "AX", "AAX", "DX"}:
            flags.append("rodden_low_or_uncertain")
    if row["source"] == "cbdb" and row["date_precision"] in {9, 10}:
        flags.append("cbdb_partial_date")
    return flags


def _native_quality(row: sqlite3.Row) -> dict[str, object]:
    """保留来源自己的质量字段，避免把来源评级混成一个分数。"""
    if row["source"] == "adb":
        return {
            "rodden_rating": row["rodden_rating"],
            "time_accuracy": row["time_accuracy"],
            "time_unknown": bool(row["time_unknown"]),
        }
    return {
        "date_precision": row["date_precision"],
        "birth_month": row["birth_month"],
        "birth_day": row["birth_day"],
    }


def profile_quality(db_path: Path, rule_version: str = RULE_VERSION) -> dict[str, int]:
    """为全部人物生成质量档案，返回各分析层级数量。"""
    conn = init_db(db_path)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    counts: dict[str, int] = {}
    try:
        rows = conn.execute(
            """SELECT id, source, entry_type, birth_date, birth_year, birth_month,
                      birth_day, birth_hour, birth_minute, birth_time,
                      rodden_rating, date_precision, time_accuracy, time_unknown
               FROM persons ORDER BY id"""
        )
        batch = []
        for row in rows:
            date_quality = _date_quality(row)
            time_quality = _time_quality(row)
            tier = _analysis_tier(date_quality, time_quality)
            counts[tier] = counts.get(tier, 0) + 1
            batch.append(
                (
                    row["id"], row["source"], row["rodden_rating"],
                    json.dumps(_native_quality(row), ensure_ascii=False),
                    date_quality, time_quality, tier,
                    json.dumps(_quality_flags(row, date_quality, time_quality), ensure_ascii=False),
                    rule_version, generated_at,
                )
            )
            if len(batch) >= 5000:
                _write_batch(conn, batch)
                batch.clear()
        if batch:
            _write_batch(conn, batch)
        conn.commit()
        return counts
    finally:
        conn.close()


def _write_batch(conn: sqlite3.Connection, batch: list[tuple[object, ...]]) -> None:
    conn.executemany(
        """INSERT INTO person_quality_profiles(
               person_id, source, native_rating, native_quality_json,
               date_quality, time_quality, analysis_tier, quality_flags,
               rule_version, generated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(person_id) DO UPDATE SET
               source = excluded.source, native_rating = excluded.native_rating,
               native_quality_json = excluded.native_quality_json,
               date_quality = excluded.date_quality, time_quality = excluded.time_quality,
               analysis_tier = excluded.analysis_tier, quality_flags = excluded.quality_flags,
               rule_version = excluded.rule_version, generated_at = excluded.generated_at""",
        batch,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="生成统一分析质量档案")
    parser.add_argument("--db", type=Path, default=Path("data/numerology.db"))
    parser.add_argument("--rule-version", default=RULE_VERSION)
    args = parser.parse_args()
    counts = profile_quality(args.db, args.rule_version)
    print("统一分析质量档案完成：" + "，".join(f"{key}={value}" for key, value in sorted(counts.items())))


if __name__ == "__main__":
    main()
