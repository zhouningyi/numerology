#!/usr/bin/env python3
"""将 events 原始表转换为 events_normalized。"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from numerology.analysis.event_normalization import (
    flags_json,
    normalize_event_date,
    split_event_code,
)
from numerology.db.schema import init_db


def normalize_events(db_path: Path) -> tuple[int, int, int]:
    """执行事件标准化，返回（总数、warning 数、invalid 数）。"""
    conn = init_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT e.id, e.person_id, p.source, e.event_code, e.event_date,
                   e.event_time, e.event_notes, e.event_place
            FROM events e
            JOIN persons p ON p.id = e.person_id
            ORDER BY e.id
            """
        ).fetchall()

        values = []
        for row in rows:
            event_type, event_subtype = split_event_code(row["event_code"])
            normalized = normalize_event_date(row["event_date"])
            values.append(
                (
                    row["id"],
                    row["person_id"],
                    row["source"],
                    event_type,
                    event_subtype,
                    normalized.start,
                    normalized.end,
                    normalized.precision,
                    row["event_time"],
                    row["event_notes"],
                    row["event_place"],
                    normalized.status,
                    flags_json(normalized.flags),
                )
            )

        conn.executemany(
            """
            INSERT INTO events_normalized
                (event_id, person_id, source, event_type, event_subtype,
                 date_start, date_end, date_precision, event_time,
                 event_notes, event_place, quality_status, quality_flags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                person_id = excluded.person_id,
                source = excluded.source,
                event_type = excluded.event_type,
                event_subtype = excluded.event_subtype,
                date_start = excluded.date_start,
                date_end = excluded.date_end,
                date_precision = excluded.date_precision,
                event_time = excluded.event_time,
                event_notes = excluded.event_notes,
                event_place = excluded.event_place,
                quality_status = excluded.quality_status,
                quality_flags = excluded.quality_flags
            """,
            values,
        )
        conn.commit()

        warning_count = conn.execute(
            "SELECT COUNT(*) FROM events_normalized WHERE quality_status='warning'"
        ).fetchone()[0]
        invalid_count = conn.execute(
            "SELECT COUNT(*) FROM events_normalized WHERE quality_status='invalid'"
        ).fetchone()[0]
        return len(values), warning_count, invalid_count
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="标准化生平事件日期")
    parser.add_argument("--db", type=Path, default=Path("data/numerology.db"))
    args = parser.parse_args()
    total, warnings, invalid = normalize_events(args.db)
    print(f"事件标准化完成：总数={total}，日期不完整={warnings}，无效={invalid}")


if __name__ == "__main__":
    main()
