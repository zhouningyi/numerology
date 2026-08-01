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
class AdbEvent:
    """ADB 生平事件（从 ==Events== 的 ASTRODATABANK_evn 模板解析）。"""

    event_code: str  # 如 "Relationship : Marriage"
    event_date: str  # YYYY/MM/DD（可能含 00）
    event_time: Optional[str]  # HH:MM or None
    event_notes: str  # 简要描述
    event_place: Optional[str]  # 地点


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
    events: list[AdbEvent] | None = None  # 生平事件列表
    # 时区信息（真太阳时校正所需）
    tz_meridian: Optional[str] = None  # 原始 stmerid，如 'h5w' / 'm10e0'
    tz_abbr: Optional[str] = None  # 时区缩写，如 'EST' / 'LMT' / 'GDT'
    time_type: Optional[str] = None  # 'standard time' / 'local mean time' / ...
    # 出生时间精度（决定时柱插补的不确定性宽度）
    time_accuracy: Optional[str] = None  # 如 'Fifteen minutes' / '2 hours'
    time_unknown: int = 0  # 1 = ADB 明确标注时间未知
    # 西洋占星星座（作为统计分析的对照组）
    sun_degmin: Optional[str] = None
    moon_degmin: Optional[str] = None
    asc_degmin: Optional[str] = None
    # 派生字段（由 classify_entry 填充）
    entry_type: str = "person"  # 'person', 'event', 'other'
    last_name: Optional[str] = None
    first_name: Optional[str] = None


def _parse_coord(coord_str: str) -> Optional[float]:
    """Parse ADB coordinate format into decimal degrees.

    ADB 用方向字母分隔度与「分秒」，分秒部分位数决定含义：
        '10e0'      -> 10°00'      -> 10.0
        '48n24'     -> 48°24'      -> 48.4
        '112e2830'  -> 112°28'30"  -> 112.475   (度分秒)
        '71w07'     -> -71°07'     -> -71.1167

    注意：早期实现把 4 位的分秒串整个当作「分」，
    112e2830 被算成 112 + 2830/60 = 159.17°（偏差 47 度）。
    """
    if not coord_str:
        return None
    m = re.match(r"(\d+)([nsew])(\d*)", coord_str.strip().lower())
    if not m:
        return None

    degrees = int(m.group(1))
    direction = m.group(2)
    rest = m.group(3)

    # 分秒串：≤2 位为「分」，≥3 位为「分(2) + 秒(余下)」
    if not rest:
        minutes = seconds = 0
    elif len(rest) <= 2:
        minutes, seconds = int(rest), 0
    else:
        minutes, seconds = int(rest[:2]), int(rest[2:4].ljust(2, "0"))

    result = degrees + minutes / 60.0 + seconds / 3600.0
    if direction in ("s", "w"):
        result = -result
    return round(result, 4)


def _parse_meridian(merid_str: str) -> Optional[float]:
    """Parse ADB 时区中央经线字段 (stmerid) 为十进制经度。

    两种格式：
        'm10e0'     地方平时 (LMT)，中央经线即出生地经度 -> 校正量为 0
        'm112e2830' 同上（度分秒）
        'h5w'       标准时区 UTC-5  -> 中央经线 -75°
        'h1e'       标准时区 UTC+1  -> 中央经线 +15°
        'h5e30'     半小时时区 UTC+5:30 -> 中央经线 +82.5°

    ADB 的 stmerid 已包含夏令时偏移（如英国夏令时记为 h1e），
    因此无需另行处理 DST。
    """
    if not merid_str:
        return None
    s = merid_str.strip().lower()

    if s.startswith("m"):
        # 地方平时：中央经线就是出生地经度
        return _parse_coord(s[1:])

    m = re.match(r"h(\d+)([ew])(\d*)", s)
    if not m:
        return None
    hours = int(m.group(1))
    minutes = int(m.group(3)) if m.group(3) else 0
    offset_hours = hours + minutes / 60.0
    if m.group(2) == "w":
        offset_hours = -offset_hours
    return round(offset_hours * 15.0, 4)


def _normalize_longitude(longitude: Optional[float]) -> Optional[float]:
    """将跨越日期变更线的经度归一化到 [-180, 180]。"""
    if longitude is None:
        return None
    while longitude > 180:
        longitude -= 360
    while longitude < -180:
        longitude += 360
    return round(longitude, 4)


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


def _classify_entry(name: str, gender: str, categories: list[str]) -> str:
    """判断条目类型：person / event / other。

    规则：
    1. 有 gender (M/F) → person
    2. name 以 4 位数字开头（如 "2015 Paris attacks"）→ event
    3. categories 含 "Mundane" → event
    4. name 不含逗号且不含空格后大写（非 "Last, First" 格式）→ other
    5. 其余 → person
    """
    if gender in ("M", "F"):
        return "person"
    if re.match(r"^\d{4}\b", name):
        return "event"
    cats_lower = " ".join(categories).lower()
    if "mundane" in cats_lower:
        return "event"
    return "person"


def _split_name(name: str) -> tuple[Optional[str], Optional[str]]:
    """拆分 ADB 格式姓名 'Last, First' → (last_name, first_name)。

    特殊情况：
    - 无逗号 → (name, None)  可能是单名或事件名
    - 多个逗号 → 只按第一个逗号拆
    """
    if "," not in name:
        return name.strip(), None
    parts = name.split(",", 1)
    return parts[0].strip(), parts[1].strip() or None


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


def _parse_events(wikitext: str) -> list[AdbEvent]:
    """提取 ==Events== 段落中所有 ASTRODATABANK_evn 模板，返回事件列表。"""
    # 找到 Events 段落
    section = re.search(r"==\s*Events\s*==(.*?)(?=\n==|\Z)", wikitext, re.DOTALL)
    if not section:
        return []

    events = []
    # 匹配每个 {{ASTRODATABANK_evn ... }}
    for m in re.finditer(
        r"\{\{ASTRODATABANK_evn(.*?)\}\}", section.group(1), re.DOTALL
    ):
        fields = {}
        for line in m.group(1).split("\n"):
            line = line.strip()
            if line.startswith("|") and "=" in line:
                key, _, val = line[1:].partition("=")
                fields[key.strip()] = val.strip()

        event_code = fields.get("sevcode", "")
        event_date = fields.get("sevdate", "")
        if not event_code or not event_date:
            continue

        events.append(
            AdbEvent(
                event_code=event_code,
                event_date=event_date,
                event_time=fields.get("sevtime") or None,
                event_notes=fields.get("EventNotes", ""),
                event_place=(fields.get("Place") or "").strip() or None,
            )
        )

    # 按日期排序
    events.sort(key=lambda e: e.event_date)
    return events


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

    def _api_get(self, params: dict, max_retries: int = 5) -> dict:
        """Make a GET request to the MediaWiki API (with exponential backoff retry)."""
        params["format"] = "json"
        for attempt in range(max_retries):
            self._throttle()
            try:
                resp = self.session.get(API_URL, params=params, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.HTTPError,
            ) as e:
                wait = min(2 ** (attempt + 1), 60)  # 2, 4, 8, 16, 32s（上限60s）
                logger.warning(
                    f"API request failed (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {wait}s..."
                )
                if attempt == max_retries - 1:
                    raise
                time.sleep(wait)
                # 重建 session 防止连接池损坏
                self.session.close()
                self.session = requests.Session()
                self.session.headers.update({"User-Agent": USER_AGENT})
        return {}  # unreachable

    def list_all_pages(
        self, start_from: Optional[str] = None
    ) -> Iterator[tuple[int, str]]:
        """Yield (page_id, page_title) for all pages.

        Args:
            start_from: 从指定 title 开始枚举（用于断点续采）
        """
        params = {
            "action": "query",
            "list": "allpages",
            "aplimit": "500",
            "apnamespace": "0",
        }
        if start_from:
            params["apfrom"] = start_from
            logger.info(f"Resuming page listing from: {start_from}")
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

    def fetch_pages_content_by_ids(self, page_ids: list[int]) -> dict[int, tuple[str, str]]:
        """按 page_id 批量获取页面，返回 ``page_id -> (title, wikitext)``。"""
        result: dict[int, tuple[str, str]] = {}
        for i in range(0, len(page_ids), 50):
            batch = page_ids[i : i + 50]
            data = self._api_get(
                {
                    "action": "query",
                    "pageids": "|".join(str(page_id) for page_id in batch),
                    "prop": "revisions",
                    "rvprop": "content",
                }
            )
            pages = data.get("query", {}).get("pages", {})
            for page_id, page in pages.items():
                if int(page_id) < 0:
                    continue
                revisions = page.get("revisions", [])
                if revisions:
                    result[int(page_id)] = (
                        page.get("title", ""),
                        revisions[0].get("*", ""),
                    )
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
        events = _parse_events(wikitext)

        name = fields.get("Name", page_title)
        gender = fields.get("Gender", "")
        entry_type = _classify_entry(name, gender, categories)
        last_name, first_name = _split_name(name)

        return AdbPerson(
            page_id=page_id,
            page_title=page_title,
            name=name,
            gender=gender,
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
            events=events,
            tz_meridian=fields.get("stmerid") or None,
            tz_abbr=fields.get("TmZnAbbr") or None,
            time_type=fields.get("stimetype") or None,
            time_accuracy=fields.get("stimeacc") or None,
            time_unknown=1 if fields.get("t_unknown") else 0,
            sun_degmin=fields.get("sun_degmin") or None,
            moon_degmin=fields.get("moon_degmin") or None,
            asc_degmin=fields.get("asc_degmin") or None,
            entry_type=entry_type,
            last_name=last_name,
            first_name=first_name,
        )

    def collect(
        self, limit: Optional[int] = None, start_from: Optional[str] = None
    ) -> Iterator[AdbPerson]:
        """Main collection loop: list pages, fetch content, parse.

        Args:
            limit: Maximum number of persons to collect (None=all)
            start_from: 从指定 title 开始（用于断点续采）
        """
        collected = 0
        page_buffer = []

        for page_id, title in self.list_all_pages(start_from=start_from):
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
