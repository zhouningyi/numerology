#!/usr/bin/env python3
"""古籍语料分层标注（实施计划 P2.5 第 1 步）。

两件事：
1. 把滴天髓爬虫快照（raw JSONL）清洗落到 data/processed/canon/ditiansui_online.txt，
   与其他三本书流水线对齐；raw 目录保持只读。
2. 对四本书的 processed 文本逐段打层级标签，输出
   data/processed/canon/layers/<book>_layers.jsonl。

层级取值：原文（经文/原著正文）、原注（刘基原注）、评注（徐注/任氏曰/眉批）、
现代白话（网站白话译文）、站点内容（关键词/现代启示等网站生成内容，不入统计）。

网页版的"原文"标签指评注本全书（含评注），不可作为原著/评注分层依据；
本脚本只在遇到显式标记（【徐注】等）时给 high 置信，其余为 low，
低置信段落必须经人工校勘（rule_status: candidate → verified）后才能用于规则提取。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

PROCESSED_DIR = Path("data/processed/canon")
LAYERS_DIR = PROCESSED_DIR / "layers"
DITIANSUI_RAW = Path("data/raw/canon/web/luckclub/ditiansui_pages.jsonl")

BOOKS = {
    "ziping_zhenquan": {"title": "子平真诠", "commentary_markers": ["徐注"]},
    "yuanhai_ziping": {"title": "渊海子平", "commentary_markers": ["眉批"]},
    "ditiansui": {"title": "滴天髓阐微", "commentary_markers": ["任氏曰"]},
    "sanming_tonghui": {"title": "三命通会", "commentary_markers": []},
}

SECTION_TEXT = "text"        # 网站"原文"区块：评注本全书文本
SECTION_BAIHUA = "baihua"    # 网站白话译文
SECTION_SITE = "site"        # 关键词/现代启示等网站生成内容

MARKER_RE = re.compile(r"^\*{0,2}【(徐注|原注|任氏曰|眉批)】")
PAGE_RE = re.compile(r"^第\s*[一二三四五六七八九十百零〇0-9\s]+页$")
CHAPTER_RE = re.compile(r"^(?:《[^》]+》)?第\s*(\d+)\s*章(?:\s+(\S.*))?$")

_LABELS = {
    "原文": SECTION_TEXT,
    "白话译文": SECTION_BAIHUA,
    "关键词": SECTION_SITE,
    "现代启示": SECTION_SITE,
}

_BOILERPLATE_SUBSTR = (
    "luckclub",
    "古籍典藏",
    "下载本书",
    "复制链接",
    "返回八字分类",
    "字号 小 中 大",
    "内容仅供文化学习研究",
)
_BOILERPLATE_EXACT = {
    "首页", "八字", "中医", "易经", "风水", "目录", "译", "/", "---",
    "原 文", "白 话 译 文", "章节目录",
}


def _section_label(line: str) -> str | None:
    """整行只是区块标签时返回区块名，否则 None。"""
    return _LABELS.get(line.replace(" ", "").replace("　", ""))


def _is_boilerplate(line: str) -> bool:
    if line in _BOILERPLATE_EXACT:
        return True
    return any(s in line for s in _BOILERPLATE_SUBSTR)


def tag_lines(lines: list[str], commentary_markers: list[str]):
    """逐行打 (layer, confidence, chapter, marker) 标签。

    状态机：区块标签切换 section；显式【标记】把 text 区块切到对应层（high）；
    "原文"标签或换页把 text 区块拉回原著层（low，因网页标签不区分原著与评注）。
    """
    section = SECTION_TEXT
    layer, confidence, marker = "原文", "low", None
    chapter: int | None = None
    for raw in lines:
        line = raw.strip()
        if not line or _is_boilerplate(line):
            continue
        m = CHAPTER_RE.match(line)
        if m:
            chapter = int(m.group(1))
            section, layer, confidence, marker = SECTION_TEXT, "原文", "low", None
            continue
        label = _section_label(line)
        if label:
            section = label
            if label == SECTION_TEXT:
                layer, confidence, marker = "原文", "low", None
            continue
        if section == SECTION_BAIHUA:
            yield "现代白话", "high", chapter, None, line
            continue
        if section == SECTION_SITE:
            yield "站点内容", "high", chapter, None, line
            continue
        if PAGE_RE.match(line):
            layer, confidence, marker = "原文", "low", None
            continue
        mk = MARKER_RE.match(line)
        if mk:
            name = mk.group(1)
            layer = "原注" if name == "原注" else "评注"
            confidence, marker = "high", name
            yield layer, confidence, chapter, marker, line
            confidence = "low"  # 标记行之后的延续行不再是显式证据
            continue
        yield layer, confidence, chapter, marker, line


def merge_segments(book: str, tagged) -> list[dict]:
    """把连续同层行合并为段落记录。"""
    segments: list[dict] = []
    for layer, confidence, chapter, marker, line in tagged:
        prev = segments[-1] if segments else None
        if (
            prev
            and prev["layer"] == layer
            and prev["chapter"] == chapter
            and prev["marker"] == marker
            and prev["confidence"] == confidence
        ):
            prev["text"] += "\n" + line
        else:
            segments.append({
                "book": book,
                "chapter": chapter,
                "layer": layer,
                "marker": marker,
                "confidence": confidence,
                "text": line,
            })
    for index, seg in enumerate(segments):
        seg["segment_index"] = index
    return segments


def build_ditiansui_txt() -> Path:
    """raw JSONL → processed 文本，保留章节标题与区块标签供分层复用。"""
    out = PROCESSED_DIR / "ditiansui_online.txt"
    parts: list[str] = []
    with DITIANSUI_RAW.open(encoding="utf-8") as handle:
        for row in map(json.loads, handle):
            if row["chapter"] == 0:  # 目录页只有站点导航
                continue
            heading = re.search(r"《滴天髓阐微》第\s*(\d+)\s*章", row["text"])
            number = int(heading.group(1)) if heading else row["chapter"]
            parts.append(f"第 {number} 章")
            parts.append(row["text"])
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def process_book(book: str, config: dict) -> tuple[Path, Counter]:
    source = PROCESSED_DIR / f"{book}_online.txt"
    lines = source.read_text(encoding="utf-8").splitlines()
    segments = merge_segments(book, tag_lines(lines, config["commentary_markers"]))
    LAYERS_DIR.mkdir(parents=True, exist_ok=True)
    out = LAYERS_DIR / f"{book}_layers.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for seg in segments:
            handle.write(json.dumps(seg, ensure_ascii=False) + "\n")
    stats = Counter((seg["layer"], seg["confidence"]) for seg in segments)
    return out, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--books", nargs="*", default=list(BOOKS))
    args = parser.parse_args()

    if "ditiansui" in args.books:
        path = build_ditiansui_txt()
        print(f"滴天髓 processed 文本 -> {path}")
    for book in args.books:
        out, stats = process_book(book, BOOKS[book])
        total = sum(stats.values())
        detail = "，".join(
            f"{layer}/{conf}: {count}" for (layer, conf), count in sorted(stats.items())
        )
        print(f"{BOOKS[book]['title']}: {total} 段 -> {out}")
        print(f"  {detail}")


if __name__ == "__main__":
    main()
