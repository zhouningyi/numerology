#!/usr/bin/env python3
"""将扫描版 OCR 页与网站原著章节做保守的标题映射。

映射只回答“这一页最可能属于哪个章节”，不把 OCR 文字当作校勘结论。
默认先使用标题的规范化精确包含，再用较高阈值的模糊匹配生成待复核候选。
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


# 古籍标题中最常见的繁简差异；正文仍保留 OCR 原貌，不在此处改写。
_TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "題": "题", "記": "记", "與": "与", "論": "论", "陰": "阴", "陽": "阳", "淵": "渊",
        "屬": "属", "從": "从", "為": "为", "開": "开", "關": "关", "於": "于",
        "後": "后", "並": "并", "無": "无", "傳": "传", "書": "书", "來": "来",
        "體": "体", "氣": "气", "節": "节", "萬": "万", "與": "与", "應": "应",
        "會": "会", "總": "总", "說": "说", "內": "内", "雜": "杂", "財": "财",
        "貴": "贵", "時": "时", "飛": "飞", "馬": "马", "傷": "伤", "殺": "杀",
        "歲": "岁", "扶": "扶", "兩": "两", "俱": "俱", "異": "异", "華": "华",
        "驛": "驿", "學": "学", "祿": "禄", "陰": "阴", "陽": "阳", "庫": "库",
        "斷": "断", "婦": "妇", "兒": "儿", "親": "亲", "為": "为", "壽": "寿",
        "醫": "医", "選": "选", "擇": "择", "過": "过", "運": "运", "與": "与",
        "詳": "详", "解": "解", "釋": "释", "賦": "赋", "訣": "诀", "詩": "诗", "評": "评", "註": "注",
        "這": "这", "個": "个", "種": "种", "類": "类", "實": "实", "現": "现",
        "當": "当", "來": "来", "與": "与", "義": "义", "錯": "错", "誤": "误",
        "訛": "讹", "尅": "克", "衝": "冲", "宮": "宫", "變": "变",
        "純": "纯", "敗": "败", "緊": "紧", "綬": "绶", "刦": "劫", "點": "点",
    }
)

# 扫描页中标题连续合排、OCR 无法可靠拆开时的人工页级补标注。
# 这不是把整页文字判定为单章；page_details 会保留同页的多个章节。
_MANUAL_PAGE_OVERRIDES: dict[str, dict[int, list[int]]] = {
    # 第 5—9 页是目录；第 32 章标题在第 63 页，第 64 页为续页。
    "ziping_zhenquan_scan_edition_a": {
        63: [32],
        64: [32],
    },
    "ditiansui_ming_scan_edition_a": {
        35: [31],
        36: [32, 33],
        37: [34, 35],
        38: [36],
        39: [36],
    },
    "ditiansui_qiong_tong_scan_edition_a": {
        28: [29, 30],
        29: [31, 32],
        30: [33, 34, 35],
        31: [36],
    },
}


def normalize_title(value: str) -> str:
    """消除空白、标点及常见繁简差异，供标题定位使用。"""
    value = unicodedata.normalize("NFKC", value).translate(_TRADITIONAL_TO_SIMPLIFIED)
    return "".join(char for char in value if not char.isspace() and not re.match(r"[\W_]", char, re.UNICODE))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def chapters_from_layers(path: Path) -> list[dict[str, Any]]:
    chapters: dict[int, dict[str, Any]] = {}
    for record in load_jsonl(path):
        chapter = int(record.get("chapter", 0) or 0)
        title = str(record.get("chapter_title") or "").strip()
        if chapter <= 0 or not title or chapter in chapters:
            continue
        chapters[chapter] = {
            "chapter": chapter,
            "title": title,
            "normalized_title": normalize_title(title),
        }
    return [chapters[key] for key in sorted(chapters)]


def title_match(title: str, page_text: str, blocks: list[dict[str, Any]] | None = None) -> tuple[str, float, str | None]:
    """返回匹配方法、分数和匹配片段。"""
    normalized_title = normalize_title(title)
    fragments = [str(block.get("text") or "") for block in (blocks or [])]
    fragments.extend(page_text.splitlines())
    normalized_fragments = [normalize_title(fragment) for fragment in fragments if fragment.strip()]
    for fragment in normalized_fragments:
        if normalized_title and normalized_title in fragment:
            offset = fragment.find(normalized_title)
            # 标题应出现在识别框/行的开头，或本身就是一个短框；
            # 避免把序文正文中偶然出现的“知命”等词当作章节标题。
            if offset == 0:
                return "exact_normalized", 1.0, normalized_title

    # OCR 可能把标题拆成多个框或错一两个字；只对较长标题做保守候选。
    if len(normalized_title) < 4 or not normalized_fragments:
        return "none", 0.0, None
    best_score = 0.0
    best_fragment = None
    for fragment in normalized_fragments:
        if len(fragment) > len(normalized_title) + 20:
            fragment = fragment[: len(normalized_title) + 20]
        score = difflib.SequenceMatcher(None, normalized_title, fragment).ratio()
        if score > best_score:
            best_score, best_fragment = score, fragment
    if best_score >= 0.75:
        return "fuzzy_candidate", best_score, best_fragment
    return "none", best_score, best_fragment


def build_mapping(
    book: str,
    source_id: str,
    layers: Path,
    ocr_root: Path,
    content_start_page: int = 1,
    min_title_length: int = 1,
) -> Path:
    chapters = chapters_from_layers(layers)
    ocr_path = ocr_root / source_id / "ocr.jsonl"
    pages = load_jsonl(ocr_path)
    candidates: list[dict[str, Any]] = []
    for page in pages:
        if int(page.get("page_pdf", 0) or 0) < content_start_page:
            continue
        page_text = str(page.get("text_raw") or "")
        for chapter in chapters:
            if len(chapter["normalized_title"]) < min_title_length:
                continue
            method, score, fragment = title_match(chapter["title"], page_text, page.get("blocks"))
            if method != "none":
                candidates.append(
                    {
                        "page_pdf": page.get("page_pdf"),
                        "chapter": chapter["chapter"],
                        "chapter_title": chapter["title"],
                        "method": method,
                        "score": round(score, 4),
                        "matched_fragment": fragment,
                    }
                )

    # 同页可能连续排有多个小章节标题，因此不能简单地“一页只留一个标题”。
    # 自动边界只采用精确匹配；模糊结果保留在 review 文件中供人工判断。
    candidates.sort(key=lambda item: (item["page_pdf"], item["chapter"], -item["score"]))
    accepted: list[dict[str, Any]] = []
    last_chapter = 0
    for candidate in candidates:
        if candidate["method"] not in {"exact_normalized", "fuzzy_candidate"} or candidate["chapter"] <= last_chapter:
            continue
        if candidate["method"] == "fuzzy_candidate":
            # 第 1 章的序文常有 OCR 一字误识，允许作为“待复核”起点；
            # 其余章节必须达到更高阈值，避免正文中的短词抢先跳到后文。
            if candidate["chapter"] == 1 and int(candidate["page_pdf"]) > 3:
                continue
            if candidate["chapter"] != 1 and candidate["score"] < 0.85:
                continue
        accepted.append(candidate)
        last_chapter = int(candidate["chapter"])

    page_numbers = sorted(int(page["page_pdf"]) for page in pages if page.get("page_pdf") is not None)
    if accepted and accepted[0]["chapter"] > 1 and page_numbers and page_numbers[0] == 1:
        first_page_text = "\n".join(
            str(page.get("text_raw") or "")
            for page in pages
            if int(page.get("page_pdf", 0) or 0) < int(accepted[0]["page_pdf"])
        )
        if "渊海子平" in first_page_text.translate(_TRADITIONAL_TO_SIMPLIFIED) and "引" in first_page_text:
            accepted.insert(
                0,
                {
                    "page_pdf": page_numbers[0],
                    "chapter": 1,
                    "chapter_title": chapters[0]["title"],
                    "method": "prefix_inference",
                    "score": 0.85,
                    "matched_fragment": "渊海子平…引",
                },
            )

    page_map: dict[str, Any] = {
        "book": book,
        "source_id": source_id,
        "method": "OCR 标题定位；精确匹配自动采用，模糊匹配仅作候选",
        "content_start_page": content_start_page,
        "min_title_length": min_title_length,
        "pages": {},
        "page_details": {},
        "headings": accepted,
        "unresolved_chapters": [],
        "manual_overrides": [],
    }
    heading_pages = sorted({int(heading["page_pdf"]) for heading in accepted})
    for index, first in enumerate(heading_pages):
        headings_on_page = [heading for heading in accepted if int(heading["page_pdf"]) == first]
        last = page_numbers[-1] if index + 1 == len(heading_pages) else heading_pages[index + 1] - 1
        primary = headings_on_page[0]
        for page in page_numbers:
            if first <= page <= last:
                page_map["pages"][str(page)] = int(primary["chapter"])
                page_map["page_details"].setdefault(str(page), {
                    "chapters": [],
                    "chapter_titles": [],
                    "heading_page": first,
                    "mapping_status": "自动标题匹配",
                })
                detail = page_map["page_details"][str(page)]
                headings_for_detail = headings_on_page if page == first else [primary]
                for heading in headings_for_detail:
                    if heading["chapter"] not in detail["chapters"]:
                        detail["chapters"].append(heading["chapter"])
                        detail["chapter_titles"].append(heading["chapter_title"])
                if any(heading["method"] != "exact_normalized" for heading in headings_for_detail):
                    detail["mapping_status"] = "待人工复核"

    title_by_chapter = {chapter["chapter"]: chapter["title"] for chapter in chapters}
    for page, override_chapters in _MANUAL_PAGE_OVERRIDES.get(source_id, {}).items():
        if page not in page_numbers:
            continue
        detail = page_map["page_details"].setdefault(str(page), {
            "chapters": [],
            "chapter_titles": [],
            "heading_page": page,
            "mapping_status": "人工补标注",
        })
        detail["chapters"] = list(override_chapters)
        detail["chapter_titles"] = [title_by_chapter.get(chapter, "") for chapter in override_chapters]
        detail["heading_page"] = page
        detail["mapping_status"] = "人工补标注"
        page_map["pages"][str(page)] = override_chapters[0]
        page_map["manual_overrides"].append({
            "page_pdf": page,
            "chapters": override_chapters,
            "chapter_titles": detail["chapter_titles"],
            "status": "待结合扫描图复核",
        })

    matched_chapters = {item["chapter"] for item in accepted}
    for override in page_map["manual_overrides"]:
        matched_chapters.update(override["chapters"])
    page_map["unresolved_chapters"] = [
        chapter["chapter"] for chapter in chapters if chapter["chapter"] not in matched_chapters
    ]

    source_dir = ocr_root / source_id
    map_path = source_dir / "page_map.json"
    map_path.write_text(json.dumps(page_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    review_path = source_dir / "chapter_mapping_review.json"
    review_path.write_text(
        json.dumps(
            {
                "book": book,
                "source_id": source_id,
                "matched_headings": accepted,
                "manual_overrides": page_map["manual_overrides"],
                "candidate_count": len(candidates),
                "unresolved_chapters": page_map["unresolved_chapters"],
                "note": "自动映射用于定位；标题识别或页码边界仍应结合扫描图人工复核。",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return map_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--layers", type=Path, default=None)
    parser.add_argument("--ocr-root", type=Path, default=Path("data/processed/canon/ocr"))
    parser.add_argument("--content-start-page", type=int, default=None, help="跳过扫描本的序言/目录/版权页")
    parser.add_argument("--min-title-length", type=int, default=1, help="标题定位的最短字数；大书可设为 3 减少短词误报")
    args = parser.parse_args()
    layers = args.layers or Path("data/processed/canon/layers") / f"{args.book}_layers.jsonl"
    default_content_start = {
        "ziping_zhenquan_scan_edition_a": 10,
        "ditiansui_ming_scan_edition_a": 6,
        "ditiansui_qiong_tong_scan_edition_a": 1,
    }.get(args.source_id, 1)
    print(build_mapping(
        args.book,
        args.source_id,
        layers,
        args.ocr_root,
        args.content_start_page if args.content_start_page is not None else default_content_start,
        args.min_title_length,
    ))


if __name__ == "__main__":
    main()
