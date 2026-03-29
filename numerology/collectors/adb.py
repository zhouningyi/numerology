"""Astro-Databank collector via MediaWiki API.

Fetches person data from https://www.astro.com/wiki/astro-databank/api.php
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import requests

logger = logging.getLogger(__name__)

API_URL = "https://www.astro.com/wiki/astro-databank/api.php"
USER_AGENT = "NumerologyResearch/1.0 (academic research)"
CRAWL_DELAY = 2.0  # seconds, as per robots.txt


@dataclass
class AdbPerson:
    """Parsed person record from Astro-Databank."""

    page_id: int
    page_title: str
    name: str
    gender: str
    birth_date: str  # YYYY/MM/DD
    birth_time: Optional[str]  # HH:MM or None
    birth_place: str
    birth_country: str
    latitude: Optional[str]  # e.g. "48n24"
    longitude: Optional[str]  # e.g. "10e0"
    rodden_rating: str
    data_source: str
    sun_sign: str
    moon_sign: str
    asc_sign: str
    categories: list[str]
    biography: Optional[str] = None  # 传记文本（从 ==Biography== 段落提取）


def _parse_coord(coord_str: str) -> Optional[float]:
    """Parse ADB coordinate format (e.g. '48n24' -> 48.4, '10e0' -> 10.0).

    Format: degrees + direction(n/s/e/w) + minutes
    """
    if not coord_str:
        return None
    m = re.match(r"(\d+)([nsew])(\d*)", coord_str.strip().lower())
    if not m:
        return None
    degrees = int(m.group(1))
    direction = m.group(2)
    minutes = int(m.group(3)) if m.group(3) else 0
    result = degrees + minutes / 60.0
    if direction in ("s", "w"):
        result = -result
    return round(result, 4)


def _parse_template(wikitext: str) -> Optional[dict]:
    """Extract ASTRODATABANK_dma template fields from wikitext."""
    match = re.search(r"\{\{ASTRODATABANK_dma(.*?)\}\}", wikitext, re.DOTALL)
    if not match:
        return None

    fields = {}
    for line in match.group(1).split("\n"):
        line = line.strip()
        if line.startswith("|") and "=" in line:
            key, _, val = line[1:].partition("=")
            fields[key.strip()] = val.strip()
    return fields


def _parse_categories(wikitext: str) -> list[str]:
    """Extract [[Category:...]] from wikitext."""
    return re.findall(r"\[\[Category:(.+?)\]\]", wikitext)


def _parse_biography(wikitext: str) -> Optional[str]:
    """提取 ==Biography== 段落的纯文本内容。

    去除 wikitext 标记（链接、模板、HTML 标签等），返回干净的文本。
    """
    # 匹配 ==Biography== 到下一个 == 段落之间的内容
    match = re.search(r"==\s*Biography\s*==\s*\n(.*?)(?=\n==|\Z)", wikitext, re.DOTALL)
    if not match:
        return None

    text = match.group(1).strip()
    if not text:
        return None

    # 清理 wikitext 标记
    # 移除 {{...}} 模板（可能嵌套，简单处理：去掉单层）
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    # 移除残余的 {{ 和 }}
    text = re.sub(r"\{\{|\}\}", "", text)
    # [[Link|Display]] -> Display; [[Link]] -> Link
    text = re.sub(r"\[\[[^]]*\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    # 移除外部链接 [url text] -> text
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\]", "", text)
    # 移除 HTML 标签
    text = re.sub(r"<[^>]+>", "", text)
    # 移除多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip() or None


class AdbCollector:
    """Collects person data from Astro-Databank via MediaWiki API."""

    def __init__(self, crawl_delay: float = CRAWL_DELAY):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.crawl_delay = crawl_delay
        self._last_request_time = 0.0

    def _throttle(self):
        """Respect crawl delay."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.crawl_delay:
            time.sleep(self.crawl_delay - elapsed)
        self._last_request_time = time.time()

    def _api_get(self, params: dict) -> dict:
        """Make a GET request to the MediaWiki API."""
        self._throttle()
        params["format"] = "json"
        resp = self.session.get(API_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def list_all_pages(self) -> Iterator[tuple[int, str]]:
        """Yield (page_id, page_title) for all pages."""
        params = {
            "action": "query",
            "list": "allpages",
            "aplimit": "500",
            "apnamespace": "0",
        }
        while True:
            data = self._api_get(params)
            for page in data.get("query", {}).get("allpages", []):
                yield page["pageid"], page["title"]
            cont = data.get("continue")
            if not cont:
                break
            params["apcontinue"] = cont["apcontinue"]

    def fetch_pages_content(self, titles: list[str]) -> dict[str, str]:
        """Fetch wikitext content for multiple pages (max 50)."""
        result = {}
        for i in range(0, len(titles), 50):
            batch = titles[i : i + 50]
            data = self._api_get(
                {
                    "action": "query",
                    "titles": "|".join(batch),
                    "prop": "revisions",
                    "rvprop": "content",
                }
            )
            pages = data.get("query", {}).get("pages", {})
            for pid, pdata in pages.items():
                if int(pid) < 0:
                    continue
                revs = pdata.get("revisions", [])
                if revs:
                    result[pdata["title"]] = revs[0].get("*", "")
        return result

    def parse_person(
        self, page_id: int, page_title: str, wikitext: str
    ) -> Optional[AdbPerson]:
        """Parse a single page's wikitext into an AdbPerson."""
        fields = _parse_template(wikitext)
        if not fields:
            return None

        # Skip if no birth date
        sbdate = fields.get("sbdate", "")
        if not sbdate or sbdate == "0000/00/00":
            return None

        categories = _parse_categories(wikitext)
        biography = _parse_biography(wikitext)

        return AdbPerson(
            page_id=page_id,
            page_title=page_title,
            name=fields.get("Name", page_title),
            gender=fields.get("Gender", ""),
            birth_date=sbdate,
            birth_time=fields.get("sbtime") or None,
            birth_place=fields.get("Place", ""),
            birth_country=fields.get("BirthCountry", ""),
            latitude=fields.get("slati") or None,
            longitude=fields.get("slong") or None,
            rodden_rating=fields.get("sroddenrating", ""),
            data_source=fields.get("sdatasource", ""),
            sun_sign=fields.get("sun_sign", ""),
            moon_sign=fields.get("moon_sign", ""),
            asc_sign=fields.get("asc_sign", ""),
            categories=categories,
            biography=biography,
        )

    def collect(self, limit: Optional[int] = None) -> Iterator[AdbPerson]:
        """Main collection loop: list pages, fetch content, parse.

        Args:
            limit: Maximum number of persons to collect (None=all)
        """
        collected = 0
        page_buffer = []

        for page_id, title in self.list_all_pages():
            page_buffer.append((page_id, title))

            if len(page_buffer) >= 50:
                titles = [t for _, t in page_buffer]
                contents = self.fetch_pages_content(titles)

                for pid, ptitle in page_buffer:
                    wikitext = contents.get(ptitle)
                    if not wikitext:
                        continue
                    person = self.parse_person(pid, ptitle, wikitext)
                    if person:
                        yield person
                        collected += 1
                        if limit and collected >= limit:
                            return

                page_buffer.clear()

        # Process remaining buffer
        if page_buffer:
            titles = [t for _, t in page_buffer]
            contents = self.fetch_pages_content(titles)
            for pid, ptitle in page_buffer:
                wikitext = contents.get(ptitle)
                if not wikitext:
                    continue
                person = self.parse_person(pid, ptitle, wikitext)
                if person:
                    yield person
                    collected += 1
                    if limit and collected >= limit:
                        return
