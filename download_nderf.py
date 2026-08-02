#!/usr/bin/env python3
"""NDERF（nderf.org）濒死体验案例采集：本地研究快照。

两个阶段：
  --stage index  抓归档列表（archivelist + 当前页 + exceptional），产出案例 URL 清单
  --stage pages  逐篇抓案例页原始 HTML（JSONL 按年分片，断点续抓）

只保存原始快照供本地研究，不对外分发；礼貌抓取（默认 0.6s 间隔）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

USER_AGENT = "numerology-research/0.1 (local research snapshot; contact mahlerzhou@gmail.com)"
RAW_DIR = Path("data/raw/nderf")
INDEX_PATH = RAW_DIR / "experience_index.jsonl"
PAGES_DIR = RAW_DIR / "pages"

ARCHIVE_LIST = "https://www.nderf.org/Archives/archivelist.htm"
EXTRA_INDEX_PAGES = [
    "https://www.nderf.org/Archives/NDERF_NDEs.html",   # 当前页
    "https://www.nderf.org/Archives/exceptional.html",  # 精选页
]
EXPERIENCE_RE = re.compile(r'href="(https?://www\.nderf\.org/Experiences/[^"]+)"')
ARCHIVE_PAGE_RE = re.compile(r'href="(https?://www\.nderf\.org/Archives/2_[^"]+\.html?)"')


def fetch(session: requests.Session, url: str, timeout: int = 60) -> bytes:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def build_index(session: requests.Session, delay: float) -> int:
    """归档列表 → 全量案例 URL 清单（含来源归档页，便于按年分片）。"""
    listing = fetch(session, ARCHIVE_LIST).decode("utf-8", errors="replace")
    archive_pages = sorted(set(ARCHIVE_PAGE_RE.findall(listing))) + EXTRA_INDEX_PAGES
    seen: dict[str, str] = {}
    for page_url in archive_pages:
        try:
            html = fetch(session, page_url).decode("utf-8", errors="replace")
        except requests.RequestException as exc:
            print(f"归档页失败 {page_url}: {exc}")
            continue
        found = EXPERIENCE_RE.findall(html)
        for url in found:
            seen.setdefault(url, page_url)
        print(f"{page_url} -> {len(found)} 条")
        time.sleep(delay)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w", encoding="utf-8") as handle:
        for url, source in sorted(seen.items()):
            handle.write(json.dumps({"url": url, "archive_page": source}) + "\n")
    print(f"案例总数 {len(seen)} -> {INDEX_PATH}")
    return len(seen)


def _shard_name(archive_page: str) -> str:
    match = re.search(r"(\d{4})", archive_page.rsplit("/", 1)[-1])
    return match.group(1) if match else "misc"


def _already_fetched() -> set[str]:
    done = set()
    if not PAGES_DIR.exists():
        return done
    for shard in PAGES_DIR.glob("pages_*.jsonl"):
        with shard.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    done.add(json.loads(line)["url"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def fetch_pages(session: requests.Session, delay: float, limit: int | None) -> None:
    """按清单抓案例页；已抓过的跳过，可随时中断续跑。"""
    if not INDEX_PATH.exists():
        raise SystemExit("请先运行 --stage index 生成案例清单")
    with INDEX_PATH.open(encoding="utf-8") as handle:
        entries = [json.loads(line) for line in handle]
    done = _already_fetched()
    todo = [e for e in entries if e["url"] not in done]
    print(f"清单 {len(entries)} 条，已抓 {len(done)}，本次待抓 {len(todo)}")
    if limit:
        todo = todo[:limit]
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    handles: dict[str, object] = {}
    count = fail = 0
    try:
        for entry in todo:
            shard = _shard_name(entry["archive_page"])
            if shard not in handles:
                handles[shard] = (PAGES_DIR / f"pages_{shard}.jsonl").open(
                    "a", encoding="utf-8"
                )
            try:
                raw = fetch(session, entry["url"])
            except requests.RequestException as exc:
                fail += 1
                print(f"失败 {entry['url']}: {exc}")
                time.sleep(delay)
                continue
            record = {
                "url": entry["url"],
                "archive_page": entry["archive_page"],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "html": raw.decode("utf-8", errors="replace"),
            }
            handles[shard].write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
            if count % 100 == 0:
                for h in handles.values():
                    h.flush()
                print(f"进度 {count}/{len(todo)}（失败 {fail}）")
            time.sleep(delay)
    finally:
        for h in handles.values():
            h.close()
    print(f"完成：新抓 {count} 篇，失败 {fail} 篇")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["index", "pages"], required=True)
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    if args.stage == "index":
        build_index(session, args.delay)
    else:
        fetch_pages(session, args.delay, args.limit)


if __name__ == "__main__":
    main()
