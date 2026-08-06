#!/usr/bin/env python3
"""把古籍网页分层文本和 OCR 记录拆成按章节的独立文件。

网页分层文本可以立即拆分；OCR 记录只有在识别完成并带有章节号，或存在
page_map.json 时，才会进入对应章节。未能归属章节的 OCR 保留在 unassigned.jsonl。
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


LAYERS_DIR = Path("data/processed/canon/layers")
OCR_DIR = Path("data/processed/canon/ocr")
DEFAULT_OUTPUT = Path("data/processed/canon/chapters")
BOOKS = ("ziping_zhenquan", "yuanhai_ziping", "ditiansui", "sanming_tonghui")


def load_jsonl(path: Path) -> list[dict]:
    """读取 JSONL，跳过空行。"""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    """写入稳定、可追溯的 JSONL。"""
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def chapter_title(rows: list[dict], chapter: int) -> str:
    """从章节记录中取得章节标题。"""
    for row in rows:
        if row.get("chapter") == chapter and row.get("chapter_title"):
            return str(row["chapter_title"])
    return ""


def write_chapter_markdown(path: Path, book: str, chapter: int, rows: list[dict]) -> None:
    """生成便于人工阅读的章节 Markdown；原文与辅助层分开。"""
    title = chapter_title(rows, chapter)
    sections = {"原文": [], "原注": [], "评注": [], "现代白话": [], "站点内容": []}
    for row in rows:
        sections.setdefault(row.get("layer", "其他"), []).append(row["text"])
    lines = [f"# {book} · 第 {chapter} 章" + (f" · {title}" if title else ""), ""]
    for layer, texts in sections.items():
        if not texts:
            continue
        lines.extend([f"## {layer}", "", "\n\n".join(texts), ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def split_web_book(book: str, output_root: Path) -> dict:
    """将网页分层文本按章节拆分。"""
    source = LAYERS_DIR / f"{book}_layers.jsonl"
    rows = load_jsonl(source)
    groups: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        chapter = row.get("chapter")
        if chapter is not None:
            groups[int(chapter)].append(row)
    destination = output_root / book
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"book": book, "source": str(source), "chapters": []}
    for chapter, chapter_rows in sorted(groups.items()):
        write_jsonl(destination / f"chapter_{chapter:03d}.jsonl", chapter_rows)
        write_chapter_markdown(
            destination / f"chapter_{chapter:03d}.md", book, chapter, chapter_rows
        )
        manifest["chapters"].append({
            "chapter": chapter,
            "title": chapter_title(chapter_rows, chapter),
            "segments": len(chapter_rows),
            "original": sum(row.get("layer") == "原文" for row in chapter_rows),
            "translation": sum(row.get("layer") == "现代白话" for row in chapter_rows),
            "commentary": sum(row.get("layer") in {"原注", "评注"} for row in chapter_rows),
        })
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"book": book, "chapters": len(groups), "segments": len(rows)}


def read_page_map(source_id: str) -> dict[int, int]:
    """读取 PDF 页码到章节的人工映射。"""
    path = OCR_DIR / source_id / "page_map.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    page_values = raw.get("pages", raw) if isinstance(raw, dict) else {}
    result = {}
    for page, chapter in page_values.items():
        try:
            if isinstance(chapter, dict):
                chapter = chapter.get("chapter")
            result[int(page)] = int(chapter)
        except (TypeError, ValueError):
            continue
    return result


def read_page_chapters(source_id: str) -> dict[int, list[int]]:
    """读取一页内的全部章节，兼容旧版单章节 page_map。"""
    path = OCR_DIR / source_id / "page_map.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result = {}
    details = raw.get("page_details", {}) if isinstance(raw, dict) else {}
    for page, detail in details.items() if isinstance(details, dict) else []:
        try:
            result[int(page)] = [int(chapter) for chapter in detail.get("chapters", [])]
        except (TypeError, ValueError):
            continue
    for page, chapter in read_page_map(source_id).items():
        result.setdefault(page, [chapter])
    return result


def split_ocr_source(source_id: str, output_root: Path) -> dict:
    """将 OCR JSONL 按记录章节或页码映射拆分。"""
    source = OCR_DIR / source_id / "ocr.jsonl"
    rows = load_jsonl(source)
    page_map = read_page_map(source_id)
    page_chapters = read_page_chapters(source_id)
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        chapter = row.get("chapter")
        if chapter is None:
            chapters = page_chapters.get(int(row["page_pdf"]), [])
            if not chapters and page_map.get(int(row["page_pdf"])) is not None:
                chapters = [page_map[int(row["page_pdf"])] ]
            if chapters:
                for chapter in chapters:
                    groups[f"chapter_{int(chapter):03d}"].append(row)
                continue
            groups["unassigned"].append(row)
            continue
        groups[f"chapter_{int(chapter):03d}"].append(row)
    destination = output_root / "ocr" / source_id
    destination.mkdir(parents=True, exist_ok=True)
    for key, chapter_rows in sorted(groups.items()):
        write_jsonl(destination / f"{key}.jsonl", chapter_rows)
    manifest = {
        "source_id": source_id,
        "source": str(source),
        "chapters": {key: len(value) for key, value in sorted(groups.items())},
        "unassigned": len(groups.get("unassigned", [])),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"source_id": source_id, "chapters": len(groups), "records": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--books", nargs="*", default=list(BOOKS))
    parser.add_argument("--ocr-source", nargs="*", help="指定 OCR 版本；默认处理全部已有 OCR")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    for book in args.books:
        print(json.dumps(split_web_book(book, args.output_root), ensure_ascii=False))
    sources = args.ocr_source
    if sources is None:
        sources = [path.name for path in sorted(OCR_DIR.iterdir()) if (path / "ocr.jsonl").exists()] if OCR_DIR.exists() else []
    for source_id in sources:
        print(json.dumps(split_ocr_source(source_id, args.output_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
