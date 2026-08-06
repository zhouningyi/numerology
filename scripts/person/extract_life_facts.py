#!/usr/bin/env python3
"""将已有的结构化事件和分类统一写入 biography_facts。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from numerology.db.schema import init_db


EXTRACTOR_VERSION = "structured-v1"


def _upsert_event_facts(conn: sqlite3.Connection) -> int:
    """导入标准化事件，不修改原始 events 表。"""
    rows = conn.execute(
        """SELECT n.id, n.person_id, n.source, n.event_type, n.event_subtype,
                      n.date_start, n.date_end, n.date_precision, e.event_code,
                      n.event_notes, n.event_place, n.quality_status, n.quality_flags
               FROM events_normalized n
               JOIN events e ON e.id = n.event_id
               ORDER BY n.id"""
    ).fetchall()
    values = []
    for row in rows:
        value_text = row["event_notes"] or row["event_subtype"] or row["event_type"] or row["event_code"]
        status_confidence = {"valid": 1.0, "warning": 0.8, "invalid": 0.2}.get(
            row["quality_status"], 0.5
        )
        metadata = {
            "event_code": row["event_code"],
            "event_type": row["event_type"],
            "event_subtype": row["event_subtype"],
            "quality_status": row["quality_status"],
            "quality_flags": json.loads(row["quality_flags"])
            if row["quality_flags"]
            else [],
        }
        values.append(
            (
                row["person_id"], row["source"], "event",
                row["event_subtype"] or row["event_type"],
                row["date_start"], row["date_end"], row["date_precision"],
                value_text, row["event_place"], row["event_notes"],
                "events_normalized", str(row["id"]), "structured_event",
                EXTRACTOR_VERSION, status_confidence, "accepted",
                json.dumps(metadata, ensure_ascii=False),
            )
        )
    conn.executemany(
        """INSERT INTO biography_facts(
               person_id, source, fact_type, fact_subtype, date_start, date_end,
               date_precision, value_text, place, evidence_text, source_table,
               source_id, extraction_method, extractor_version, confidence,
               review_status, metadata_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(person_id, source_table, source_id, fact_type, fact_subtype, value_text)
           DO UPDATE SET
               source = excluded.source, date_start = excluded.date_start,
               date_end = excluded.date_end, date_precision = excluded.date_precision,
               place = excluded.place, evidence_text = excluded.evidence_text,
               confidence = excluded.confidence, metadata_json = excluded.metadata_json,
               extractor_version = excluded.extractor_version""",
        values,
    )
    return len(values)


def _upsert_category_facts(conn: sqlite3.Connection) -> int:
    """将来源分类复制为无日期生平事实，保留分类原文。"""
    rows = conn.execute(
        """SELECT c.id, c.person_id, p.source, c.category, c.cat_type
               FROM categories c JOIN persons p ON p.id = c.person_id
               ORDER BY c.id"""
    ).fetchall()
    values = [
        (
            row["person_id"], row["source"], "category", row["cat_type"],
            row["category"], row["category"], "categories", str(row["id"]),
            "structured_category", EXTRACTOR_VERSION, 1.0, "pending",
            json.dumps({"category_type": row["cat_type"]}, ensure_ascii=False),
        )
        for row in rows
    ]
    conn.executemany(
        """INSERT INTO biography_facts(
               person_id, source, fact_type, fact_subtype, value_text,
               evidence_text, source_table, source_id, extraction_method,
               extractor_version, confidence, review_status, metadata_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(person_id, source_table, source_id, fact_type, fact_subtype, value_text)
           DO UPDATE SET
               source = excluded.source, evidence_text = excluded.evidence_text,
               confidence = excluded.confidence, metadata_json = excluded.metadata_json,
               extractor_version = excluded.extractor_version""",
        values,
    )
    return len(values)


def extract_life_facts(db_path: Path, include: str = "all") -> tuple[int, int]:
    """导入事件和/或分类，返回（事件事实数、分类事实数）。"""
    conn = init_db(db_path)
    try:
        event_count = _upsert_event_facts(conn) if include in {"all", "events"} else 0
        category_count = (
            _upsert_category_facts(conn) if include in {"all", "categories"} else 0
        )
        conn.commit()
        return event_count, category_count
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="生成结构化生平事实")
    parser.add_argument("--db", type=Path, default=Path("data/numerology.db"))
    parser.add_argument(
        "--include", choices=("all", "events", "categories"), default="all",
        help="导入事件、分类或全部",
    )
    args = parser.parse_args()
    event_count, category_count = extract_life_facts(args.db, args.include)
    print(f"生平事实生成完成：事件={event_count}，分类={category_count}")


if __name__ == "__main__":
    main()
