#!/usr/bin/env python3
"""抓取命理古籍在线阅读页面到本地研究快照。

只保存原始网页和可检索文本，不把网页内容直接写入规则表。
项目定位是命理学总览：八字只是其中一个体系，书目注册表按体系分类。
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
OUTPUT_ROOT = Path("data/raw/canon/web/luckclub")

# luckclub.cn 书目注册表：slug → 分类、站内编号、书名、体系
# category/book_id 对应站点 URL /<category>/<book_id>/
LUCKCLUB_BOOKS: dict[str, dict] = {
    # 八字：已在项目核心语料
    "yuanhai_ziping": {"category": "bazi", "book_id": "001", "title": "渊海子平", "system": "八字"},
    "ziping_zhenquan": {"category": "bazi", "book_id": "002", "title": "子平真诠", "system": "八字"},
    "sanming_tonghui": {"category": "bazi", "book_id": "005", "title": "三命通会", "system": "八字"},
    "ditiansui": {"category": "bazi", "book_id": "007", "title": "滴天髓阐微", "system": "八字"},
    # 八字：补充书目
    "qianli_minggao": {"category": "bazi", "book_id": "003", "title": "千里命稿", "system": "八字"},
    "qiongtong_baojian": {"category": "bazi", "book_id": "004", "title": "穷通宝鉴", "system": "八字"},
    "ditiansui_yuanwen": {"category": "bazi", "book_id": "006", "title": "滴天髓（原文本）", "system": "八字"},
    "shenfeng_tongkao": {"category": "bazi", "book_id": "008", "title": "神峰通考", "system": "八字"},
    "mingli_tanyuan": {"category": "bazi", "book_id": "009", "title": "命理探原", "system": "八字"},
    "lixuzhong_mingshu": {"category": "bazi", "book_id": "010", "title": "李虚中命书", "system": "八字古法"},
    "wuxing_dayi": {"category": "bazi", "book_id": "011", "title": "五行大义", "system": "五行理论"},
    "yuzhao_dingzhenjing": {"category": "bazi", "book_id": "012", "title": "玉照定真经", "system": "八字古法"},
    "wuxing_jingji": {"category": "bazi", "book_id": "013", "title": "五行精纪", "system": "八字"},
    "xingping_huihai": {"category": "bazi", "book_id": "014", "title": "星平会海", "system": "星命"},
    "bazi_tiyao": {"category": "bazi", "book_id": "015", "title": "八字提要", "system": "八字"},
    "guigu_yiwen": {"category": "bazi", "book_id": "016", "title": "鬼谷遗文", "system": "八字古法"},
    "guagua_ji": {"category": "bazi", "book_id": "017", "title": "呱呱集", "system": "八字"},
    "mingli_yueyan": {"category": "bazi", "book_id": "018", "title": "命理约言", "system": "八字"},
    "zaohua_yuanyue": {"category": "bazi", "book_id": "019", "title": "造化元钥评注", "system": "八字"},
    "ziping_guanjian": {"category": "bazi", "book_id": "020", "title": "子平管见", "system": "八字"},
    "yuding_ziping": {"category": "bazi", "book_id": "021", "title": "御定子平", "system": "八字"},
    "xingming_zongkuo": {"category": "bazi", "book_id": "022", "title": "星命总括", "system": "星命"},
    "yuetan_fu": {"category": "bazi", "book_id": "023", "title": "月谈赋", "system": "八字"},
    "luoluzi_xiaoxifu": {"category": "bazi", "book_id": "024", "title": "珞琭子消息赋", "system": "八字古法"},
    "xingming_juegulu": {"category": "bazi", "book_id": "025", "title": "星命抉古录", "system": "星命"},
    "lantai_miaoxuan": {"category": "bazi", "book_id": "026", "title": "兰台妙选原文", "system": "八字"},
    # 易经：占卜体系的经典底本
    "yijing": {"category": "yijing", "book_id": "001", "title": "易经", "system": "易经"},
    "yizhuan": {"category": "yijing", "book_id": "002", "title": "易传", "system": "易经"},
    "dongpo_yizhuan": {"category": "yijing", "book_id": "003", "title": "东坡易传", "system": "易经"},
}


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
    parser.add_argument(
        "--books", nargs="*", default=None,
        help="书目 slug 列表；缺省时配合 --all 下载注册表全部书目",
    )
    parser.add_argument("--all", action="store_true", help="下载注册表中全部书目")
    parser.add_argument(
        "--skip-existing", action="store_true", help="已有 JSONL 的书目跳过不重抓",
    )
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()

    if args.books:
        slugs = args.books
    elif args.all:
        slugs = list(LUCKCLUB_BOOKS)
    else:
        parser.error("请指定 --books <slug…> 或 --all")

    unknown = [s for s in slugs if s not in LUCKCLUB_BOOKS]
    if unknown:
        parser.error(f"未注册的书目：{unknown}；可用：{', '.join(LUCKCLUB_BOOKS)}")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    for slug in slugs:
        book = LUCKCLUB_BOOKS[slug]
        output = OUTPUT_ROOT / f"{slug}_pages.jsonl"
        if args.skip_existing and output.exists():
            print(f"跳过（已存在）：{book['title']} -> {output}")
            continue
        base_url = f"https://www.luckclub.cn/{book['category']}/{book['book_id']}/"
        count = crawl_book(session, base_url, output, args.delay)
        print(f"抓取完成：{book['title']} {count} 个页面 -> {output}")


if __name__ == "__main__":
    main()
