#!/usr/bin/env python3
"""从扫描 PDF 记录页面图片，供版本核验使用。

图片记录独立于 OCR：先保存页面图像和哈希，之后再补 OCR、章节和人工校勘状态。
默认只处理指定页，避免一次性渲染整套大部头扫描本。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_OUTPUT = Path("data/processed/canon/scans")


def sha256_file(path: Path) -> str:
    """计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(path: Path) -> str:
    """计算页面图片 SHA-256。"""
    return sha256_file(path)


def parse_pages(value: str) -> tuple[int, int]:
    """解析 1 或 1-3 形式的 PDF 页码。"""
    if "-" in value:
        first_text, last_text = value.split("-", 1)
        first, last = int(first_text), int(last_text)
    else:
        first = last = int(value)
    if first < 1 or last < first:
        raise ValueError("页码必须是正整数范围")
    return first, last


def render(pdf: Path, first: int, last: int, dpi: int, page_dir: Path) -> list[Path]:
    """使用 pdftoppm 将指定页面转换为 PNG。"""
    page_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="numerology-scan-") as temporary:
        prefix = Path(temporary) / "page"
        subprocess.run(
            [
                "pdftoppm", "-f", str(first), "-l", str(last),
                "-r", str(dpi), "-png", str(pdf), str(prefix),
            ],
            check=True,
        )
        rendered = sorted(Path(temporary).glob("page-*.png"))
        result = []
        for index, source in enumerate(rendered, start=first):
            destination = page_dir / f"page-{index:04d}.png"
            shutil.copy2(source, destination)
            result.append(destination)
        return result


def record_images(pdf: Path, source_id: str, pages: str, dpi: int, output_root: Path) -> Path:
    """渲染页面并写入 manifest 与逐页 JSONL。"""
    first, last = parse_pages(pages)
    source_root = output_root / source_id
    page_dir = source_root / "pages"
    images = render(pdf, first, last, dpi, page_dir)
    created_at = datetime.now(timezone.utc).isoformat()
    records = [
        {
            "source_id": source_id,
            "input_pdf": str(pdf.resolve()),
            "input_sha256": sha256_file(pdf),
            "page_pdf": int(image.stem.rsplit("-", 1)[1]),
            "image": image.name,
            "image_sha256": sha256_bytes(image),
            "dpi": dpi,
            "created_at": created_at,
            "image_status": "recorded",
            "chapter": None,
        }
        for image in images
    ]
    images_path = source_root / "images.jsonl"
    with images_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    manifest = {
        "source_id": source_id,
        "input_pdf": str(pdf.resolve()),
        "input_sha256": sha256_file(pdf),
        "pages_requested": pages,
        "pages_recorded": [record["page_pdf"] for record in records],
        "dpi": dpi,
        "created_at": created_at,
        "images_jsonl": str(images_path),
        "chapter_status": "unassigned",
        "ocr_status": "not_started",
    }
    manifest_path = source_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="扫描 PDF")
    parser.add_argument("--source-id", required=True, help="扫描版本唯一编号")
    parser.add_argument("--pages", default="1", help="页码，例如 1 或 1-3")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(record_images(args.input.resolve(), args.source_id, args.pages, args.dpi, args.output_root))


if __name__ == "__main__":
    main()
