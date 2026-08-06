#!/usr/bin/env python3
"""下载个人研究用的《白话华严经》网页快照。

来源按卷分为 80 个页面。原始 HTML 单独保存，后续处理脚本只读取本地快照，
避免把网页抓取和段落对齐混在一起。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


ROOT = Path("data/raw/canon/huayan_t0279")
MODERN_ROOT = ROOT / "modern_hrfjw"
INDEX_URL = "https://www.hrfjw.com/fojing/huayanjing/180838.html"
BASE_URL = "https://www.hrfjw.com"
USER_AGENT = "numerology-research/0.1 (personal research; local archive)"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chinese_number(value: str) -> int:
    """解析目录链接中的中文卷号。"""
    digits = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return (digits.get(left, 1) if left else 1) * 10 + (digits.get(right, 0) if right else 0)
    return digits[value]


def discover_volume_urls(session: requests.Session) -> dict[int, str]:
    response = session.get(INDEX_URL, timeout=60)
    response.raise_for_status()
    response.encoding = "GB18030"
    soup = BeautifulSoup(response.text, "html.parser")
    result = {}
    for link in soup.find_all("a", href=True):
        label = " ".join(link.get_text(" ", strip=True).split())
        match = re.search(r"白话华严经\s*第([〇零一二三四五六七八九十百]+)卷", label)
        if not match:
            continue
        href = urljoin(BASE_URL, link["href"])
        if re.search(r"/18\d+\.html$", href):
            result[chinese_number(match.group(1))] = href
    if len(result) != 80:
        raise RuntimeError(f"目录未发现完整 80 卷白话页面，实际 {len(result)} 卷")
    return result


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    MODERN_ROOT.mkdir(parents=True, exist_ok=True)
    urls = discover_volume_urls(session)
    records = []
    for volume in range(1, 81):
        url = urls[volume]
        target = MODERN_ROOT / f"volume-{volume:02d}.html"
        if not target.exists():
            response = session.get(url, timeout=120)
            response.raise_for_status()
            response.encoding = "GB18030"
            target.write_text(response.text, encoding="utf-8")
            time.sleep(0.2)
        records.append({
            "volume": volume,
            "url": url,
            "path": str(target),
            "bytes": target.stat().st_size,
            "sha256": sha256(target),
        })

    manifest = {
        "book": "大方广佛华严经",
        "canonical_id": "T0279",
        "source_title": "白话华严经",
        "author": "洪启嵩",
        "source_index": INDEX_URL,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "个人研究本地保存，不对外再发布",
        "volumes": records,
    }
    (MODERN_ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    root_manifest = ROOT / "manifest.json"
    if root_manifest.exists():
        combined = json.loads(root_manifest.read_text(encoding="utf-8"))
        combined["modern_translation"] = {
            "status": "downloaded_local_research_copy",
            "source": "华人佛教网",
            "author": "洪启嵩",
            "url": INDEX_URL,
            "volumes": 80,
            "note": "仅保存本机个人研究快照；原网页与现代段落保留来源信息。",
        }
        root_manifest.write_text(
            json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({"root": str(MODERN_ROOT), "volumes": len(records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
