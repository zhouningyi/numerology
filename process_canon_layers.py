#!/usr/bin/env python3
"""古籍语料分层标注（实施计划 P2.5 第 1 步）。

两件事：
1. 把滴天髓爬虫快照（raw JSONL）清洗落到 data/processed/canon/ditiansui_online.txt，
   与其他三本书流水线对齐；raw 目录保持只读。
2. 对注册书目的 processed 文本逐段打层级标签，输出
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

from download_canon_web import LUCKCLUB_BOOKS, OUTPUT_ROOT as RAW_WEB_DIR

PROCESSED_DIR = Path("data/processed/canon")
LAYERS_DIR = PROCESSED_DIR / "layers"

# 核心四书的人工配置（评注标记等）；其余书目从下载注册表自动纳入
_CURATED = {
    "ziping_zhenquan": {"commentary_markers": ["徐注"]},
    "yuanhai_ziping": {"commentary_markers": ["眉批"]},
    "ditiansui": {"commentary_markers": ["任氏曰"]},
    "sanming_tonghui": {"commentary_markers": []},
}

# 不来自 luckclub 的古籍先登记到同一语料目录，待下载原文后再生成 layers 文件。
# 这类书只用于古籍阅读、翻译、版本和解读对照，不进入命理计算。
_EXTRA_CORPUS_BOOKS = {
    "huayan_t0279": {
        "title": "大方广佛华严经（实叉难陀译）",
        "system": "佛典",
        "corpus_group": "古籍语料",
        "calculation_scope": "不参与命理计算",
        "commentary_markers": [],
    },
}


def _build_books() -> dict[str, dict]:
    books: dict[str, dict] = {}
    for slug, meta in LUCKCLUB_BOOKS.items():
        books[slug] = {
            "title": meta["title"],
            "system": meta.get("system", ""),
            "corpus_group": (
                "古籍语料" if meta.get("category") in {"yijing", "buddhist"}
                else "命理语料"
            ),
            "calculation_scope": (
                "不参与命理计算" if meta.get("category") in {"yijing", "buddhist"}
                else "可进入命理研究流程"
            ),
            "commentary_markers": [],
        }
    for slug, extra in _CURATED.items():
        books.setdefault(slug, {"title": slug, "system": "", "commentary_markers": []})
        books[slug].update(extra)
    for slug, meta in _EXTRA_CORPUS_BOOKS.items():
        books[slug] = dict(meta)
    return books


BOOKS = _build_books()

SECTION_TEXT = "text"        # 网站"原文"区块：评注本全书文本
SECTION_BAIHUA = "baihua"    # 网站白话译文
SECTION_SITE = "site"        # 关键词/现代启示等网站生成内容

MARKER_RE = re.compile(r"^\*{0,2}【(徐注|原注|任氏曰|眉批)】")
YIJING_TRANSMISSION_RE = re.compile(r"(?=(?:彖曰|象曰|文言曰)\s*[：:])")
YIJING_YAO_RE = re.compile(
    r"(?=(?:初九|九二|九三|九四|九五|上九|初六|六二|六三|六四|六五|上六|用九|用六)\s*[：:曰])"
)
YIJING_YAO_HEAD_RE = re.compile(
    r"^(?P<label>初九|九二|九三|九四|九五|上九|初六|六二|六三|六四|六五|上六|用九|用六)\s*[：:，,曰]"
)
YIJING_YAO_SPLIT_RE = re.compile(
    r"(?=(?:初九|九二|九三|九四|九五|上九|初六|六二|六三|六四|六五|上六|用九|用六)\s*[：:，,曰])"
)
YIJING_MODERN_HEADER_RE = re.compile(
    r"^(?:卦辞|六爻|彖传|象传|文言传)(?:（[^）]+）)?\s*[：:]"
)
PAGE_RE = re.compile(r"^第\s*[一二三四五六七八九十百零〇0-9\s]+页$")
CHAPTER_RE = re.compile(r"^(?:《[^》]+》)?第\s*(\d+)\s*章(?:\s+(\S.*))?$")
# 原书自带的篇章号，如“《滴天髓阐微》上篇第30章 燥湿”；与站点目录编号（含目录/序/简介）不同
BOOK_LABEL_RE = re.compile(r"^(?:《[^》]+》)?\s*([上中下]篇第\s*\d+\s*章)\s*(.*)$")

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
    "原 文", "白 话 译 文", "章节目录", "《》", "原文 白话译文",
}


def _section_label(line: str) -> str | None:
    """整行只是区块标签时返回区块名，否则 None。"""
    return _LABELS.get(line.replace(" ", "").replace("　", ""))


def _is_boilerplate(line: str) -> bool:
    if line in _BOILERPLATE_EXACT:
        return True
    if line.startswith("首页/"):  # 面包屑导航
        return True
    return any(s in line for s in _BOILERPLATE_SUBSTR)


def tag_lines(
    lines: list[str],
    commentary_markers: list[str],
    skip_prelude: bool = False,
    chapter_titles: dict[int, str | None] | None = None,
):
    """逐行打 (layer, confidence, chapter, marker) 标签。

    状态机：区块标签切换 section；显式【标记】把 text 区块切到对应层（high）；
    "原文"标签或换页把 text 区块拉回原著层（low，因网页标签不区分原著与评注）。
    """
    section = SECTION_TEXT
    layer, confidence, marker = "原文", "low", None
    chapter: int | None = None
    seen_chapter = False
    titles = chapter_titles or {}
    for raw in lines:
        line = raw.strip()
        if not line or _is_boilerplate(line):
            continue
        m = CHAPTER_RE.match(line)
        if m:
            chapter = int(m.group(1))
            seen_chapter = True
            section, layer, confidence, marker = SECTION_TEXT, "原文", "low", None
            continue
        # 网页封面、书籍介绍和站点宣传不属于原著正文；保留在 raw 文本中，
        # 但不让它们进入原文层，也不计入章节统计。
        if skip_prelude and not seen_chapter:
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
        title = titles.get(chapter) or ""
        if title and line == title and section == SECTION_TEXT:
            continue  # 页内重复出现的章节标题行
        bl = BOOK_LABEL_RE.match(line)
        if bl:
            # 原书篇章号行是标题，不入正文；但有的行标题后直接粘着经文，需保留
            layer, confidence, marker = "原文", "low", None
            remainder = (bl.group(2) or "").strip()
            if title and remainder.startswith(title):
                remainder = remainder[len(title):].strip()
            if remainder:
                yield "原文", "low", chapter, None, remainder
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


def split_yijing_segments(segments: list[dict]) -> list[dict]:
    """把《周易》网页中一整卦的换行整理成可对照的结构段。

    原网页通常把卦辞、六爻、彖传、象传和文言传放在同一正文区块；
    白话译文则按这五类输出。只在明确的传文标题处切分，不根据语义臆断。
    """
    output: list[dict] = []
    for segment in segments:
        if segment["layer"] not in {"原文", "现代白话", "现代释译"}:
            output.append(segment)
            continue
        lines = [line.strip() for line in segment["text"].splitlines() if line.strip()]
        if len(lines) <= 1:
            item = dict(segment)
            item["section_key"] = _yijing_section_key(item["text"], item["layer"])
            output.append(item)
            continue
        groups: list[list[str]]
        if segment["layer"] == "原文":
            # 标题可能和上一句粘在同一行，先按“彖/象/文言”切开。
            sections: list[list[str]] = []
            current: list[str] = []
            for line in lines:
                chunks = [chunk.strip() for chunk in YIJING_TRANSMISSION_RE.split(line) if chunk.strip()]
                for chunk in chunks:
                    if chunk.startswith(("彖曰", "象曰", "文言曰")):
                        if current:
                            sections.append(current)
                        current = [chunk]
                    else:
                        current.append(chunk)
            if current:
                sections.append(current)
            # 第一行是卦辞；其余传文标题前的内容归为六爻。
            groups = []
            if sections:
                if len(sections[0]) > 1:
                    groups.append([sections[0][0]])
                    groups.append(sections[0][1:])
                else:
                    groups.append(sections[0])
                groups.extend(sections[1:])
            # 没有文言传时，象传常与每一爻粘在一起，按爻题拆开。
            if not any(any(line.startswith("文言曰") for line in group) for group in groups):
                refined: list[list[str]] = []
                for group in groups:
                    joined = "\n".join(group)
                    parts = [part.strip() for part in YIJING_YAO_RE.split(joined) if part.strip()]
                    if len(parts) > 1 and group[0].startswith("象曰"):
                        refined.append([parts[0]])
                        refined.extend([[part] for part in parts[1:]])
                    else:
                        refined.append(group)
                groups = refined
        else:
            # 白话区的五类标题可能在同一合并段中，标题后的解释继续归入该类。
            groups = []
            current = []
            for line in lines:
                if current and YIJING_MODERN_HEADER_RE.match(line):
                    groups.append(current)
                    current = []
                current.append(line)
            if current:
                groups.append(current)
        # 卦爻正文和现代“六爻”译文继续细拆到初九、九二等，
        # 让页面可以做到一爻原文对应一爻译文；文言传不在这里强行拆分。
        expanded_groups: list[list[str]] = []
        for group in groups:
            joined = "\n".join(group).strip()
            yao_parts = [part.strip() for part in YIJING_YAO_SPLIT_RE.split(joined) if part.strip()]
            if not joined.startswith("文言曰") and len(yao_parts) >= 2:
                expanded_groups.extend([[part] for part in yao_parts if YIJING_YAO_HEAD_RE.match(part)])
            else:
                expanded_groups.append(group)
        groups = expanded_groups
        for group_index, group in enumerate(groups):
            item = dict(segment)
            item["text"] = "\n".join(group)
            item["section_key"] = _yijing_section_key(item["text"], item["layer"])
            output.append(item)
    for index, segment in enumerate(output):
        segment["segment_index"] = index
    return output


def _yijing_section_key(text: str, layer: str) -> str | None:
    """返回可用于原文—译文对照的保守结构键（已规范化）。"""
    from numerology.corpus_quality import normalize_section_key

    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    raw: str | None = None
    if layer in {"现代白话", "现代释译"}:
        match = YIJING_MODERN_HEADER_RE.match(first)
        if match:
            raw = re.split(r"[：:]", first, maxsplit=1)[0].strip()
    if raw is None:
        for key, prefix in (("彖传", "彖曰"), ("象传", "象曰"), ("文言传", "文言曰")):
            if first.startswith(prefix):
                raw = key
                break
    if raw is None:
        yao_match = YIJING_YAO_HEAD_RE.match(first)
        if yao_match:
            raw = yao_match.group("label")
    if raw is None and layer == "原文":
        raw = "卦辞" if not first.startswith(("彖曰", "象曰", "文言曰")) else None
    return normalize_section_key(raw)


def build_txt_from_jsonl(book: str) -> Path | None:
    """raw JSONL → processed 文本，保留章节标题与区块标签供分层复用。

    返回 None 表示该书还没有 raw 快照。已有 processed 文本的书（如 PDF 转出的
    早期四书）默认不覆盖；滴天髓历来由 JSONL 重建，保持原行为。
    """
    raw = RAW_WEB_DIR / f"{book}_pages.jsonl"
    if not raw.exists():
        return None
    out = PROCESSED_DIR / f"{book}_online.txt"
    if out.exists() and book != "ditiansui":
        return out
    parts: list[str] = []
    with raw.open(encoding="utf-8") as handle:
        for row in map(json.loads, handle):
            if row["chapter"] == 0:  # 目录页只有站点导航
                continue
            heading = re.search(r"《[^》]+》第\s*(\d+)\s*章", row["text"])
            number = int(heading.group(1)) if heading else row["chapter"]
            parts.append(f"第 {number} 章")
            parts.append(row["text"])
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def extract_book_labels(lines: list[str]) -> dict[int, str]:
    """提取原书自带篇章号（如“上篇第30章”），键为站点章节号。"""
    labels: dict[int, str] = {}
    chapter: int | None = None
    for raw in lines:
        line = raw.strip()
        m = CHAPTER_RE.match(line)
        if m:
            chapter = int(m.group(1))
            continue
        bl = BOOK_LABEL_RE.match(line)
        if bl and chapter is not None and chapter not in labels:
            labels[chapter] = re.sub(r"\s+", "", bl.group(1))
    return labels


def process_book(book: str, config: dict) -> tuple[Path, Counter]:
    source = PROCESSED_DIR / f"{book}_online.txt"
    lines = source.read_text(encoding="utf-8").splitlines()
    chapter_titles = extract_chapter_titles(lines)
    book_labels = extract_book_labels(lines)
    segments = merge_segments(
        book,
        tag_lines(
            lines, config["commentary_markers"],
            skip_prelude=True, chapter_titles=chapter_titles,
        ),
    )
    if book == "yijing":
        segments = split_yijing_segments(segments)
    for segment in segments:
        segment["chapter_title"] = chapter_titles.get(segment["chapter"])
        segment["book_chapter_label"] = book_labels.get(segment["chapter"])
        if book == "dongpo_yizhuan" and segment["layer"] == "现代白话":
            # 东坡易传的现代文字是本卦级解读摘要，不是逐句翻译；
            # 即使恰好只有一个原文段，也不能据段数相等自动挂接。
            segment["alignment_status"] = "待语义对齐"
            segment["alignment_method"] = "本卦级解释，不是逐字翻译"
    LAYERS_DIR.mkdir(parents=True, exist_ok=True)
    out = LAYERS_DIR / f"{book}_layers.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for seg in segments:
            handle.write(json.dumps(seg, ensure_ascii=False) + "\n")
    stats = Counter((seg["layer"], seg["confidence"]) for seg in segments)
    return out, stats


def extract_chapter_titles(lines: list[str]) -> dict[int, str | None]:
    """提取章节标题；兼容标题与“第 N 章”分行的网页格式。"""
    titles: dict[int, str | None] = {}
    for index, raw in enumerate(lines):
        match = CHAPTER_RE.match(raw.strip())
        if not match:
            continue
        title = (match.group(2) or "").strip()
        if not title:
            for following in lines[index + 1 :]:
                candidate = following.strip()
                if not candidate or _is_boilerplate(candidate):
                    continue
                if _section_label(candidate) or PAGE_RE.match(candidate):
                    continue
                if CHAPTER_RE.match(candidate):
                    break
                title = candidate
                break
        titles[int(match.group(1))] = title or None
    return titles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--books", nargs="*", default=list(BOOKS))
    args = parser.parse_args()

    for book in args.books:
        if book == "huayan_t0279":
            print("华严经请使用 process_huayan.py，跳过通用命理网页分层器")
            continue
        built = build_txt_from_jsonl(book)
        if built is None and not (PROCESSED_DIR / f"{book}_online.txt").exists():
            print(f"跳过 {BOOKS[book]['title']}：无 raw 快照也无 processed 文本")
            continue
        out, stats = process_book(book, BOOKS[book])
        total = sum(stats.values())
        detail = "，".join(
            f"{layer}/{conf}: {count}" for (layer, conf), count in sorted(stats.items())
        )
        print(f"{BOOKS[book]['title']}: {total} 段 -> {out}")
        print(f"  {detail}")


if __name__ == "__main__":
    main()
