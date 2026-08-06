#!/usr/bin/env python3
"""将 CBDB SQLite 原始库导入项目规范化数据库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

from numerology.collectors.cbdb import CbdbPerson, CbdbReader
from numerology.db.schema import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_SOURCE_URL = (
    "https://huggingface.co/datasets/cbdb/cbdb-sqlite/"
    "resolve/main/history/cbdb_202607/cbdb_20260725.zip"
)
DEFAULT_LICENSE = "CC BY-NC-SA 4.0"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def load_metadata(input_path: Path) -> dict:
    """读取与数据库同目录的发布元数据（若存在）。"""
    metadata_path = input_path.with_suffix(".json")
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("无法读取元数据 %s：%s", metadata_path, exc)
        return {}


def _person_values(person: CbdbPerson) -> tuple:
    """生成 persons 表写入值。"""
    return (
        "cbdb",
        person.source_id,
        "person",
        person.name_chn or person.name,
        person.last_name,
        person.first_name,
        person.gender,
        person.birth_date,
        person.birth_year,
        person.birth_month,
        person.birth_day,
        person.death_date,
        person.death_year,
        person.birth_precision,
    )


def _insert_batch(
    conn: sqlite3.Connection,
    snapshot_id: int,
    people: list[CbdbPerson],
) -> int:
    """批量写入人物、来源映射和出生/死亡事实。"""
    if not people:
        return 0

    conn.executemany(
        """
        INSERT INTO persons
            (source, source_id, entry_type, name, last_name, first_name,
             gender, birth_date, birth_year, birth_month, birth_day,
             death_date, death_year, date_precision)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, source_id) DO UPDATE SET
            entry_type = excluded.entry_type,
            name = excluded.name,
            last_name = excluded.last_name,
            first_name = excluded.first_name,
            gender = excluded.gender,
            birth_date = excluded.birth_date,
            birth_year = excluded.birth_year,
            birth_month = excluded.birth_month,
            birth_day = excluded.birth_day,
            death_date = excluded.death_date,
            death_year = excluded.death_year,
            date_precision = excluded.date_precision
        """,
        [_person_values(person) for person in people],
    )

    source_ids = [person.source_id for person in people]
    placeholders = ",".join("?" for _ in source_ids)
    person_rows = conn.execute(
        f"""
        SELECT id, source_id
        FROM persons
        WHERE source = 'cbdb' AND source_id IN ({placeholders})
        """,
        source_ids,
    ).fetchall()
    person_by_source = {row["source_id"]: row["id"] for row in person_rows}

    conn.executemany(
        """
        INSERT OR IGNORE INTO source_records
            (snapshot_id, source, source_id, person_id, source_table, raw_key)
        VALUES (?, 'cbdb', ?, ?, 'BIOG_MAIN', ?)
        """,
        [
            (snapshot_id, person.source_id, person_by_source[person.source_id], person.source_id)
            for person in people
            if person.source_id in person_by_source
        ],
    )

    record_rows = conn.execute(
        f"""
        SELECT id, source_id
        FROM source_records
        WHERE snapshot_id = ? AND source = 'cbdb'
          AND source_id IN ({placeholders})
        """,
        [snapshot_id, *source_ids],
    ).fetchall()
    record_by_source = {row["source_id"]: row["id"] for row in record_rows}

    conn.executemany(
        """
        INSERT OR IGNORE INTO birth_facts
            (person_id, source_record_id, calendar, date_start, date_end,
             date_precision, raw_year, raw_month, raw_day, raw_range_code)
        VALUES (?, ?, 'cbdb', ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                person_by_source[person.source_id],
                record_by_source[person.source_id],
                person.birth_start,
                person.birth_end,
                person.birth_precision,
                person.birth_year,
                person.birth_month,
                person.birth_day,
                person.birth_range,
            )
            for person in people
            if person.source_id in person_by_source
            and person.source_id in record_by_source
            and person.birth_year is not None
        ],
    )

    conn.executemany(
        """
        INSERT OR IGNORE INTO death_facts
            (person_id, source_record_id, calendar, date_start, date_end,
             date_precision, raw_year, raw_month, raw_day, raw_range_code,
             death_age)
        VALUES (?, ?, 'cbdb', ?, ?, ?, ?, NULL, NULL, NULL, ?)
        """,
        [
            (
                person_by_source[person.source_id],
                record_by_source[person.source_id],
                person.death_start,
                person.death_end,
                person.death_precision,
                person.death_year,
                person.death_age,
            )
            for person in people
            if person.source_id in person_by_source
            and person.source_id in record_by_source
            and person.death_year is not None
        ],
    )

    return len(person_by_source)


def import_cbdb(
    input_path: Path,
    target_path: Path,
    release_name: str,
    batch_size: int,
) -> tuple[int, int, int]:
    """执行 CBDB 导入，返回（人物数、出生事实数、死亡事实数）。"""
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")

    metadata = load_metadata(input_path)
    input_sha256 = sha256_file(input_path)
    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    conn = init_db(target_path)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO source_snapshots
                (source, release_name, retrieved_at, source_url, license,
                 raw_path, sha256, metadata_json)
            VALUES ('cbdb', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                release_name,
                retrieved_at,
                metadata.get("huggingface_url", DEFAULT_SOURCE_URL),
                DEFAULT_LICENSE,
                str(input_path),
                input_sha256,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        snapshot = conn.execute(
            """
            SELECT id FROM source_snapshots
            WHERE source = 'cbdb' AND release_name = ?
            """,
            (release_name,),
        ).fetchone()
        if snapshot is None:
            raise RuntimeError("无法创建 CBDB 数据快照记录")
        snapshot_id = snapshot["id"]

        reader = CbdbReader(input_path)
        total = 0
        reader_iter = iter(reader.iter_people(batch_size))
        while True:
            # 以批次处理，避免一次性将 66 万人物全部载入内存。
            batch = list(islice(reader_iter, batch_size))
            if not batch:
                break
            total += _insert_batch(conn, snapshot_id, batch)
            conn.commit()
            if total and total % 50000 < len(batch):
                logger.info("已导入 CBDB 人物：%d", total)

        conn.execute(
            "UPDATE source_snapshots SET record_count = ? WHERE id = ?",
            (total, snapshot_id),
        )
        conn.commit()

        birth_count = conn.execute(
            "SELECT COUNT(*) FROM birth_facts WHERE source_record_id IN "
            "(SELECT id FROM source_records WHERE snapshot_id = ?)",
            (snapshot_id,),
        ).fetchone()[0]
        death_count = conn.execute(
            "SELECT COUNT(*) FROM death_facts WHERE source_record_id IN "
            "(SELECT id FROM source_records WHERE snapshot_id = ?)",
            (snapshot_id,),
        ).fetchone()[0]
        return total, birth_count, death_count
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="导入 CBDB SQLite 人物数据")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/cbdb/cbdb_20260725.sqlite3"),
        help="CBDB 原始 SQLite 路径",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/numerology.db"),
        help="项目规范化数据库路径",
    )
    parser.add_argument(
        "--release",
        default="cbdb_20260725",
        help="数据发布版本名",
    )
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()

    total, birth_count, death_count = import_cbdb(
        args.input,
        args.db,
        args.release,
        args.batch_size,
    )
    logger.info(
        "CBDB 导入完成：人物=%d，出生事实=%d，死亡事实=%d",
        total,
        birth_count,
        death_count,
    )


if __name__ == "__main__":
    main()
