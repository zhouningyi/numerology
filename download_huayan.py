#!/usr/bin/env python3
"""下载唐实叉难陀译《大方广佛华严经》（T0279）研究资料。

保存三类材料：
1. CBETA T0279 纯文本与含校注文本；
2. 维基文库八十卷原始 wikitext，作为独立录入本；
3. Wikimedia Commons 的明正统五年北藏本扫描 PDF。

现代白话译本不在没有明确开放许可时自动抓取；后续由项目生成“现代释译”层。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests


ROOT = Path("data/raw/canon/huayan_t0279")
USER_AGENT = "numerology-research/0.1 (local research; contact project owner)"
CBETA_BASE = "https://cbdata.dila.edu.tw/stable/download"
WIKISOURCE_BASE = "https://zh.wikisource.org/w/index.php?title="
WIKISOURCE_TITLE = "大方廣佛華嚴經八十卷"
SCAN_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/1/1f/"
    "NCL-08661_15_%E5%A4%A7%E6%96%B9%E5%BB%A3%E4%BD%9B%E8%8F%AF%E5%9A%B4%E7%B6%93.pdf"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(session: requests.Session, url: str, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with session.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        handle.write(chunk)
    return {"path": str(target), "url": url, "bytes": target.stat().st_size, "sha256": sha256(target)}


def extract_zip(source: Path, target_dir: Path) -> list[str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            destination = (target_dir / info.filename).resolve()
            if not str(destination).startswith(str(target_dir.resolve()) + "/"):
                raise RuntimeError(f"压缩包路径越界：{info.filename}")
            archive.extract(info, target_dir)
            names.append(info.filename)
    return names


def download_wikisource(session: requests.Session, target_dir: Path) -> list[dict]:
    target_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for volume in range(1, 81):
        suffix = f"{volume:02d}"
        title = f"{WIKISOURCE_TITLE}/{suffix}"
        url = f"{WIKISOURCE_BASE}{quote(title)}&action=raw"
        target = target_dir / f"volume-{volume:02d}.wikitext"
        if not target.exists():
            response = session.get(url, timeout=60)
            if response.status_code == 404:
                raise RuntimeError(f"维基文库卷页不存在：{title}")
            response.raise_for_status()
            target.write_text(response.text, encoding="utf-8")
        records.append({"volume": volume, "url": url, "path": str(target), "bytes": target.stat().st_size, "sha256": sha256(target)})
        time.sleep(0.15)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-scan", action="store_true", help="跳过约 48 MB 扫描 PDF")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "book": "大方广佛华严经",
        "slug": "huayan_t0279",
        "translator": "实叉难陀",
        "canonical_id": "T0279",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "sources": [],
        "modern_translation": {
            "status": "not_downloaded",
            "reason": "未确认完整现代译本的开放许可；后续生成项目自有现代释译。",
        },
    }

    for label, url, filename, extract_dir in [
        ("cbeta_text", f"{CBETA_BASE}/text/T0279.txt.zip", "T0279.txt.zip", "cbeta_text"),
        ("cbeta_text_with_notes", f"{CBETA_BASE}/text-with-notes/T0279.txt.zip", "T0279.txt.zip", "cbeta_text_with_notes"),
    ]:
        record = download(session, url, ROOT / "cbeta" / label / filename)
        record["type"] = label
        record["extracted"] = extract_zip(ROOT / "cbeta" / label / filename, ROOT / "cbeta" / label / extract_dir)
        manifest["sources"].append(record)

    wikisource_records = download_wikisource(session, ROOT / "wikisource")
    manifest["sources"].append({
        "type": "wikisource_wikitext",
        "index_url": f"https://zh.wikisource.org/wiki/{quote(WIKISOURCE_TITLE)}",
        "volumes": wikisource_records,
    })

    if not args.skip_scan:
        scan = download(session, SCAN_URL, ROOT / "scans" / "NCL-08661_15_大方廣佛華嚴經.pdf")
        scan.update({
            "type": "scan_pdf",
            "source_page": "https://commons.wikimedia.org/wiki/File:NCL-08661_15_大方廣佛華嚴經.pdf",
            "edition": "明正統五年（1440）內府刊北藏本",
            "license_note": "Wikimedia Commons 标注为 PD-scan/公有领域扫描；保留馆藏与来源信息。",
        })
        manifest["sources"].append(scan)

    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"root": str(ROOT), "sources": len(manifest["sources"]), "manifest": str(ROOT / "manifest.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
