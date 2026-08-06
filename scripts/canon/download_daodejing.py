#!/usr/bin/env python3
"""下载《道德经》王弼本的维基文库原始 wikitext，并登记来源快照。"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests


BOOK = "daodejing_wangbi"
ROOT = Path(f"data/raw/canon/{BOOK}")
TITLE = "道德經 (王弼本)"
SOURCE_PAGE = f"https://zh.wikisource.org/wiki/{quote(TITLE)}"
SOURCE_RAW = f"https://zh.wikisource.org/w/index.php?title={quote(TITLE)}&action=raw"
USER_AGENT = "numerology-research/0.1 (local research; contact project owner)"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(*, refresh: bool = False) -> dict:
    target = ROOT / "wikisource" / "daodejing_wangbi.wikitext"
    target.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not target.exists():
        response = requests.get(SOURCE_RAW, headers={"User-Agent": USER_AGENT}, timeout=60)
        response.raise_for_status()
        target.write_text(response.text, encoding="utf-8")
    manifest = {
        "book": "道德经",
        "slug": BOOK,
        "edition": "王弼本（维基文库页面所收本）",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "sources": [{
            "type": "wikisource_wikitext",
            "page_url": SOURCE_PAGE,
            "raw_url": SOURCE_RAW,
            "path": str(target),
            "bytes": target.stat().st_size,
            "sha256": sha256(target),
            "license_note": "维基文库内容依其版权政策以公有领域或 CC BY-SA 方式收录；保留来源快照。项目现代释译与标注另行生成。",
        }],
        "modern_translation": {
            "status": "project_generated_candidate",
            "reason": "避免未核实许可的完整现代译本；现代释译逐章生成并待人工复核。",
        },
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="重新下载并覆盖本地来源快照")
    args = parser.parse_args()
    manifest = download(refresh=args.refresh)
    print(json.dumps({"root": str(ROOT), "manifest": str(ROOT / "manifest.json"), "sources": len(manifest["sources"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
