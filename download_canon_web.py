#!/usr/bin/env python3
"""抓取命理古籍在线阅读页面到本地研究快照。

只保存原始网页和可检索文本，不把网页内容直接写入规则表。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests


USER_AGENT = "numerology-research/0.1 (local research snapshot)"


class TextParser(HTMLParser):
    """提取网页可读文本，跳过脚本、样式和导航噪声。"""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs):  # type: ignore[no-untyped-def]
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1
        if tag in {"p", "div", "br", "h1", "h2", "h3", "li", "tr"} and not self.skip:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1
        if tag in {"p", "div", "br", "h1", "h2", "h3", "li", "tr"} and not self.skip:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts))
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(session: requests.Session, url: str) -> bytes:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def crawl_book(session: requests.Session, base_url: str, output: Path, delay: float) -> int:
    """抓取目录页下的数字章节，输出 JSONL 快照。"""
    index_raw = fetch(session, base_url)
    index_text = index_raw.decode("utf-8", errors="replace")
    links = sorted({
        urljoin(base_url, href)
        for href in re.findall(r'href=["\']([^"\']+)', index_text)
        if re.search(r"/\d{3}/\d{3}/?$", urljoin(base_url, href))
    })
    links.insert(0, base_url)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for index, url in enumerate(links):
            raw = fetch(session, url)
            text = TextParser()
            text.feed(raw.decode("utf-8", errors="replace"))
            record = {
                "book_url": base_url,
                "url": url,
                "chapter": index,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "sha256": sha256_bytes(raw),
                "text": text.text(),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
            if index + 1 < len(links):
                time.sleep(delay)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/raw/canon/web/luckclub/ditiansui_pages.jsonl")
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    count = crawl_book(
        session,
        "https://www.luckclub.cn/bazi/007/",
        Path(args.output),
        args.delay,
    )
    print(f"抓取完成：{count} 个页面 -> {args.output}")


if __name__ == "__main__":
    main()
