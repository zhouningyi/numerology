#!/usr/bin/env python3
"""导入 CBDB 的地址、关系、科举/入仕和身份状态事实。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from numerology.db.schema import init_db


EXTRACTOR_VERSION = "cbdb-structured-v1"


def year_interval(first_year: object, last_year: object = None) -> tuple[str | None, str | None, int | None]:
    """将 CBDB 年份字段转换为年区间；负数和 0 不伪造 ISO 日期。"""
    try:
        first = int(first_year) if first_year is not None else None
    except (TypeError, ValueError):
        first = None
    try:
        last = int(last_year) if last_year is not None else None
    except (TypeError, ValueError):
        last = None
    if first is None or first <= 0:
        return None, None, None
    if last is None or last <= 0:
        last = first
    return f"{first:04d}-01-01", f"{last:04d}-12-31", 9


def _metadata(**values: object) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def _write_batch(conn: sqlite3.Connection, batch: list[tuple[object, ...]]) -> None:
    conn.executemany(
        """INSERT INTO biography_facts(
               person_id, source, fact_type, fact_subtype, date_start, date_end,
               date_precision, value_text, place, evidence_text, source_table,
               source_id, extraction_method, extractor_version, confidence,
               review_status, metadata_json
           ) VALUES (?, 'cbdb', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                     'cbdb_structured', ?, 1.0, 'pending', ?)
           ON CONFLICT(person_id, source_table, source_id, fact_type, fact_subtype, value_text)
           DO UPDATE SET
               date_start = excluded.date_start, date_end = excluded.date_end,
               date_precision = excluded.date_precision, place = excluded.place,
               evidence_text = excluded.evidence_text,
               extractor_version = excluded.extractor_version,
               metadata_json = excluded.metadata_json""",
        batch,
    )


def _flush(conn: sqlite3.Connection, batch: list[tuple[object, ...]], count: int) -> int:
    if len(batch) >= 5000:
        _write_batch(conn, batch)
        count += len(batch)
        batch.clear()
    return count


def _import_addresses(raw: sqlite3.Connection, target: sqlite3.Connection, people: dict[str, int]) -> int:
    cursor = raw.execute(
        """SELECT d.rowid AS source_rowid, d.c_personid, d.c_addr_id,
                  d.c_addr_type, d.c_firstyear, d.c_lastyear, d.c_natal,
                  d.c_notes, a.c_name_chn, a.c_name,
                  t.c_addr_desc_chn, t.c_addr_desc
           FROM BIOG_ADDR_DATA d
           LEFT JOIN ADDR_CODES a ON a.c_addr_id = d.c_addr_id
           LEFT JOIN BIOG_ADDR_CODES t ON t.c_addr_type = d.c_addr_type
           WHERE COALESCE(d.c_delete, 0) = 0"""
    )
    batch: list[tuple[object, ...]] = []
    count = 0
    for row in cursor:
        person_id = people.get(str(row["c_personid"]))
        if person_id is None:
            continue
        start, end, precision = year_interval(row["c_firstyear"], row["c_lastyear"])
        address = row["c_name_chn"] or row["c_name"] or f"地址 ID {row['c_addr_id']}"
        subtype = row["c_addr_desc_chn"] or row["c_addr_desc"] or "地址"
        batch.append(
            (
                person_id, "address", subtype, start, end, precision, address,
                address, row["c_notes"], "BIOG_ADDR_DATA", str(row["source_rowid"]),
                EXTRACTOR_VERSION,
                _metadata(addr_id=row["c_addr_id"], addr_type=row["c_addr_type"], natal=row["c_natal"]),
            )
        )
        count = _flush(target, batch, count)
    if batch:
        _write_batch(target, batch)
        count += len(batch)
    return count


def _import_relations(raw: sqlite3.Connection, target: sqlite3.Connection, people: dict[str, int]) -> int:
    cursor = raw.execute(
        """SELECT k.rowid AS source_rowid, k.c_personid, k.c_kin_id,
                  k.c_kin_code, k.c_notes, k.c_autogen_notes,
                  r.c_kinrel_chn, r.c_kinrel,
                  p.c_name_chn, p.c_name
           FROM KIN_DATA k
           LEFT JOIN KINSHIP_CODES r ON r.c_kincode = k.c_kin_code
           LEFT JOIN BIOG_MAIN p ON p.c_personid = k.c_kin_id"""
    )
    batch: list[tuple[object, ...]] = []
    count = 0
    for row in cursor:
        person_id = people.get(str(row["c_personid"]))
        if person_id is None:
            continue
        relation = row["c_kinrel_chn"] or row["c_kinrel"] or "亲属关系未详"
        target_name = row["c_name_chn"] or row["c_name"] or f"人物 ID {row['c_kin_id']}"
        evidence = row["c_notes"] or row["c_autogen_notes"]
        batch.append(
            (
                person_id, "relation", relation, None, None, None,
                f"{relation}：{target_name}", None, evidence, "KIN_DATA",
                str(row["source_rowid"]), EXTRACTOR_VERSION,
                _metadata(kin_id=row["c_kin_id"], kin_code=row["c_kin_code"]),
            )
        )
        count = _flush(target, batch, count)
    if batch:
        _write_batch(target, batch)
        count += len(batch)
    return count


def _import_entries(raw: sqlite3.Connection, target: sqlite3.Connection, people: dict[str, int]) -> int:
    cursor = raw.execute(
        """SELECT e.rowid AS source_rowid, e.c_personid, e.c_entry_code,
                  e.c_year, e.c_notes, e.c_pages,
                  c.c_entry_desc_chn, c.c_entry_desc
           FROM ENTRY_DATA e
           LEFT JOIN ENTRY_CODES c ON c.c_entry_code = e.c_entry_code"""
    )
    return _import_code_rows(
        cursor, target, people, "entry", "ENTRY_DATA", "entry_code", "entry",
    )


def _import_statuses(raw: sqlite3.Connection, target: sqlite3.Connection, people: dict[str, int]) -> int:
    cursor = raw.execute(
        """SELECT s.rowid AS source_rowid, s.c_personid, s.c_status_code,
                  s.c_firstyear, s.c_lastyear, s.c_supplement, s.c_notes, s.c_pages,
                  c.c_status_desc_chn, c.c_status_desc
           FROM STATUS_DATA s
           LEFT JOIN STATUS_CODES c ON c.c_status_code = s.c_status_code"""
    )
    return _import_code_rows(
        cursor, target, people, "status", "STATUS_DATA", "status_code", "status",
    )


def _import_code_rows(
    cursor: Iterable[sqlite3.Row], target: sqlite3.Connection, people: dict[str, int],
    fact_type: str, source_table: str, code_key: str, subtype_key: str,
) -> int:
    batch: list[tuple[object, ...]] = []
    count = 0
    for row in cursor:
        person_id = people.get(str(row["c_personid"]))
        if person_id is None:
            continue
        if fact_type == "entry":
            start, end, precision = year_interval(row["c_year"])
            subtype = row["c_entry_desc_chn"] or row["c_entry_desc"] or "入仕/科举"
            value = subtype
            code = row["c_entry_code"]
        else:
            start, end, precision = year_interval(row["c_firstyear"], row["c_lastyear"])
            subtype = row["c_status_desc_chn"] or row["c_status_desc"] or "身份状态"
            value = subtype
            code = row["c_status_code"]
        supplement = row["c_supplement"] if "c_supplement" in row.keys() else None
        pages = row["c_pages"] if "c_pages" in row.keys() else None
        batch.append(
            (
                person_id, subtype_key, subtype, start, end, precision, value,
                None, row["c_notes"] or supplement or pages,
                source_table, str(row["source_rowid"]), EXTRACTOR_VERSION,
                _metadata(**{code_key: code}),
            )
        )
        count = _flush(target, batch, count)
    if batch:
        _write_batch(target, batch)
        count += len(batch)
    return count


def enrich_cbdb(input_path: Path, db_path: Path, include: str = "all") -> dict[str, int]:
    """导入 CBDB 关联事实并返回各类导入数量。"""
    target = init_db(db_path)
    raw = sqlite3.connect(f"file:{input_path.resolve()}?mode=ro", uri=True)
    raw.row_factory = sqlite3.Row
    try:
        people = {
            str(row["source_id"]): row["id"]
            for row in target.execute("SELECT id, source_id FROM persons WHERE source='cbdb'")
        }
        counts = {}
        if include in {"all", "addresses"}:
            counts["addresses"] = _import_addresses(raw, target, people)
        if include in {"all", "relations"}:
            counts["relations"] = _import_relations(raw, target, people)
        if include in {"all", "entries"}:
            counts["entries"] = _import_entries(raw, target, people)
        if include in {"all", "statuses"}:
            counts["statuses"] = _import_statuses(raw, target, people)
        target.commit()
        return counts
    finally:
        raw.close()
        target.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="补充 CBDB 结构化生平事实")
    parser.add_argument("--input", type=Path, default=Path("data/raw/cbdb/cbdb_20260725.sqlite3"))
    parser.add_argument("--db", type=Path, default=Path("data/numerology.db"))
    parser.add_argument(
        "--include", choices=("all", "addresses", "relations", "entries", "statuses"),
        default="all",
    )
    args = parser.parse_args()
    print(json.dumps(enrich_cbdb(args.input, args.db, args.include), ensure_ascii=False))


if __name__ == "__main__":
    main()
