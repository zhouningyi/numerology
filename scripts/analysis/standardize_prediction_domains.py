#!/usr/bin/env python3
"""生成标准化预测域观察结果。"""

import argparse

from numerology.analysis.prediction_domains import (
    load_taxonomy,
    standardize_prediction_domains,
)
from numerology.db.schema import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/numerology.db")
    parser.add_argument("--taxonomy", default=None)
    args = parser.parse_args()

    conn = init_db(args.db)
    counts = standardize_prediction_domains(
        conn, load_taxonomy(args.taxonomy) if args.taxonomy else None
    )
    for domain, count in sorted(counts.items()):
        print(f"{domain}\t{count}")
    conn.close()


if __name__ == "__main__":
    main()
