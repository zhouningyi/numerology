#!/usr/bin/env python3
"""从 ADB 原始页面重新读取并修复越界经纬度。"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

from numerology.collectors.adb import AdbCollector, _normalize_longitude, _parse_coord
from numerology.db.schema import get_connection, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _invalid_rows(conn: sqlite3.Connection, limit: int | None) -> list[sqlite3.Row]:
    """读取需要重新向 ADB 核验的记录。"""
    sql = """
        SELECT id, source_id
        FROM persons
        WHERE source = 'adb'
          AND ((birth_lat IS NOT NULL AND (birth_lat < -90 OR birth_lat > 90))
            OR (birth_lon IS NOT NULL AND (birth_lon < -180 OR birth_lon > 180)))
        ORDER BY id
    """
    params: tuple[object, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return conn.execute(sql, params).fetchall()


def repair_coordinates(
    db_path: Path,
    delay: float = 2.0,
    limit: int | None = None,
) -> tuple[int, int, int]:
    """修复越界坐标，返回（目标数、更新数、失败数）。"""
    conn = init_db(db_path)
    try:
        rows = _invalid_rows(conn, limit)
        if not rows:
            return 0, 0, 0

        collector = AdbCollector(crawl_delay=delay)
        updated = 0
        failed = 0
        for offset in range(0, len(rows), 50):
            batch = rows[offset : offset + 50]
            page_ids = [int(row["source_id"]) for row in batch]
            pages = collector.fetch_pages_content_by_ids(page_ids)
            for row in batch:
                page_id = int(row["source_id"])
                page = pages.get(page_id)
                if page is None:
                    failed += 1
                    continue
                title, wikitext = page
                person = collector.parse_person(page_id, title, wikitext)
                if person is None:
                    failed += 1
                    continue

                lat = _parse_coord(person.latitude) if person.latitude else None
                lon = (
                    _normalize_longitude(_parse_coord(person.longitude))
                    if person.longitude
                    else None
                )
                conn.execute(
                    """
                    UPDATE persons SET
                        birth_lat = ?, birth_lon = ?,
                        birth_lat_raw = ?, birth_lon_raw = ?,
                        tz_meridian = ?, tz_abbr = ?, time_type = ?,
                        time_accuracy = ?, time_unknown = ?,
                        sun_degmin = ?, moon_degmin = ?, asc_degmin = ?
                    WHERE id = ? AND source = 'adb'
                    """,
                    (
                        lat,
                        lon,
                        person.latitude,
                        person.longitude,
                        person.tz_meridian,
                        person.tz_abbr,
                        person.time_type,
                        person.time_accuracy,
                        person.time_unknown,
                        person.sun_degmin,
                        person.moon_degmin,
                        person.asc_degmin,
                        row["id"],
                    ),
                )
                updated += 1
            conn.commit()
            logger.info(
                "坐标修复进度：%d/%d，已更新=%d，失败=%d",
                min(offset + len(batch), len(rows)),
                len(rows),
                updated,
                failed,
            )
        return len(rows), updated, failed
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="修复 ADB 越界经纬度")
    parser.add_argument("--db", type=Path, default=Path("data/numerology.db"))
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 条，用于测试")
    args = parser.parse_args()

    target, updated, failed = repair_coordinates(args.db, args.delay, args.limit)
    print(f"坐标修复完成：目标={target}，更新={updated}，失败={failed}")


if __name__ == "__main__":
    main()
