#!/usr/bin/env python3
"""Collect data from Wikidata and store in SQLite with BaZi calculations."""

import argparse
import logging
import sys

from numerology.collectors.wikidata import WikidataCollector
from numerology.db.pipeline import insert_wikidata_person
from numerology.db.schema import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Collect Wikidata birth data")
    parser.add_argument(
        "--db",
        default="data/numerology.db",
        help="SQLite database path (default: data/numerology.db)",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=1800,
        help="Start year (default: 1800)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2010,
        help="End year exclusive (default: 2010)",
    )
    parser.add_argument(
        "--year-step",
        type=int,
        default=10,
        help="Years per SPARQL batch (default: 10)",
    )
    parser.add_argument(
        "--light",
        action="store_true",
        help="Light mode: only name + DOB, no occupation/death data",
    )
    args = parser.parse_args()

    conn = init_db(args.db)
    collector = WikidataCollector()

    inserted = 0
    skipped = 0

    logger.info(f"Starting Wikidata collection: {args.start_year}-{args.end_year}")

    try:
        for person in collector.collect(
            start_year=args.start_year,
            end_year=args.end_year,
            year_step=args.year_step,
            light=args.light,
        ):
            pid = insert_wikidata_person(conn, person)
            if pid:
                inserted += 1
                if inserted % 1000 == 0:
                    conn.commit()
                    logger.info(f"Progress: {inserted} inserted, {skipped} skipped")
            else:
                skipped += 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        conn.commit()
        total = conn.execute(
            "SELECT COUNT(*) FROM persons WHERE source='wikidata'"
        ).fetchone()[0]
        with_bazi = conn.execute(
            "SELECT COUNT(*) FROM bazi b JOIN persons p ON b.person_id=p.id "
            "WHERE p.source='wikidata'"
        ).fetchone()[0]
        logger.info(f"Done. Inserted: {inserted}, Skipped: {skipped}")
        logger.info(f"Total Wikidata records in DB: {total}")
        logger.info(f"Records with BaZi computed: {with_bazi}")
        conn.close()


if __name__ == "__main__":
    main()
