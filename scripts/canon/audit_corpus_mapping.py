#!/usr/bin/env python3
"""审计濒死 / 周易 / 华严的翻译与映射质量。

输出 JSON 报告到 data/audits/corpus_mapping_<timestamp>.json，
并写一份 latest 软链接式副本便于页面读取。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from numerology.corpus_quality import run_all_audits

AUDITS_DIR = Path("data/audits")
LATEST = AUDITS_DIR / "corpus_mapping_latest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="自定义报告路径；默认写入 data/audits/corpus_mapping_<ts>.json",
    )
    parser.add_argument("--print", action="store_true", dest="do_print", help="打印摘要")
    args = parser.parse_args()

    report = run_all_audits()
    AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report["checked_at"].replace(":", "").replace("+00:00", "Z")
    path = args.output or (AUDITS_DIR / f"corpus_mapping_{stamp}.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "path": str(path),
        "latest": str(LATEST),
        "flag_count": report["flag_count"],
        "severity_counts": report["severity_counts"],
        "domains": {
            name: {
                "flags": len(body.get("flags") or []),
                "counts": body.get("counts") or body.get("layers") or body.get("generated_coverage"),
            }
            for name, body in report["domains"].items()
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.do_print:
        for flag in report["flags"]:
            print(f"[{flag['severity']}] {flag['domain']}: {flag['code']} — {flag['message']}")


if __name__ == "__main__":
    main()
