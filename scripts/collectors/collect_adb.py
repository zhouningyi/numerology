#!/usr/bin/env python3
"""Collect data from Astro-Databank and store in SQLite with BaZi calculations."""

import argparse
import logging
import sys

from numerology.collectors.adb import AdbCollector
from numerology.db.pipeline import insert_adb_person
from numerology.db.schema import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _get_resume_point(conn) -> str | None:
    """查询数据库中 ADB 来源最后一条记录的 page_title，用于断点续采。

    ADB allpages API 按字母序返回，name 字段即 page_title。
    """
    row = conn.execute(
        """SELECT name FROM persons
           WHERE source='adb'
           ORDER BY name DESC LIMIT 1"""
    ).fetchone()
    return row[0] if row else None


def main():
    parser = argparse.ArgumentParser(description="Collect Astro-Databank data")
    parser.add_argument(
        "--db",
        default="data/numerology.db",
        help="SQLite database path (default: data/numerology.db)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of persons to collect (default: all)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Crawl delay in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last collected record (断点续采)",
    )
    args = parser.parse_args()

    conn = init_db(args.db)
    collector = AdbCollector(crawl_delay=args.delay)

    # 断点续采：从最后一条记录开始
    start_from = None
    if args.resume:
        start_from = _get_resume_point(conn)
        if start_from:
            existing = conn.execute(
                "SELECT COUNT(*) FROM persons WHERE source='adb'"
            ).fetchone()[0]
            logger.info(f"Resuming from '{start_from}' ({existing} records in DB)")
        else:
            logger.info("No existing records, starting from beginning")

    inserted = 0
    skipped = 0

    logger.info(f"Starting ADB collection (limit={args.limit}, delay={args.delay}s)")

    try:
        for person in collector.collect(limit=args.limit, start_from=start_from):
            pid = insert_adb_person(conn, person)
            if pid:
                inserted += 1
                if inserted % 100 == 0:
                    conn.commit()
                    logger.info(f"Progress: {inserted} inserted, {skipped} skipped")
            else:
                skipped += 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        conn.commit()
        # Report stats
        total = conn.execute(
            "SELECT COUNT(*) FROM persons WHERE source='adb'"
        ).fetchone()[0]
        with_time = conn.execute(
            "SELECT COUNT(*) FROM bazi b JOIN persons p ON b.person_id=p.id "
            "WHERE p.source='adb' AND b.has_time_pillar=1"
        ).fetchone()[0]
        logger.info(f"Done. Inserted: {inserted}, Skipped: {skipped}")
        logger.info(f"Total ADB records in DB: {total}")
        logger.info(f"Records with full 4 pillars: {with_time}")
        conn.close()


if __name__ == "__main__":
    main()
