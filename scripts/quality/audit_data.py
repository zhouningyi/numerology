#!/usr/bin/env python3
"""运行人物数据质量审计并输出 Markdown 报告。"""

from __future__ import annotations

import argparse
from pathlib import Path

from numerology.analysis.data_quality import (
    audit_database,
    persist_quality_flags,
    render_markdown,
)
from numerology.db.schema import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="运行人物数据质量审计")
    parser.add_argument("--db", type=Path, default=Path("data/numerology.db"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--persist",
        action="store_true",
        help="将逐条质量标签写入 data_quality_flags 表",
    )
    parser.add_argument(
        "--rule-version",
        default="2026-07-31",
        help="逐条质量规则版本号",
    )
    args = parser.parse_args()

    conn = init_db(args.db)
    try:
        summaries, issues = audit_database(conn)
        persisted = None
        if args.persist:
            persisted = persist_quality_flags(conn, rule_version=args.rule_version)
    finally:
        conn.close()

    report = render_markdown(summaries, issues)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"审计报告已写入：{args.output}")
    else:
        print(report)
    if persisted is not None:
        run_id, flag_count = persisted
        print(f"逐条质量标签已写入：audit_run_id={run_id}, flags={flag_count}")


if __name__ == "__main__":
    main()
