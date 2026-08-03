#!/usr/bin/env python3
"""华严经原文小段独立翻译器。

核心约束：一次请求只翻译一个原文单元，输出只能对应这个单元，
不再把整卷/整品交给模型后再猜译文边界。结果先作为“现代释译”候选，
必须保留原文、模型、提示版本和复核状态。

示例：
    python3 translate_huayan_segments.py --chapter 1 --start-segment 5 --limit 2
    python3 translate_huayan_segments.py --chapter 1 --max-chars 700 --materialize
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests


BOOK = "huayan_t0279"
ORIGINAL_PATH = Path("data/processed/canon/layers/huayan_t0279_layers.jsonl")
OUTPUT_PATH = Path("data/processed/canon/translation_candidates/huayan_t0279_segment_translations.jsonl")
LAYER_PATH = Path("data/processed/canon/layers/huayan_t0279_generated_layers.jsonl")
REFERENCE_PATH = Path("data/processed/canon/layers/huayan_t0279_aligned_layers.jsonl")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
PROMPT_VERSION = "huayan-segment-translation-v2"


def load_originals() -> list[dict]:
    return [
        json.loads(line)
        for line in ORIGINAL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def split_source_units(text: str, max_chars: int = 700) -> list[str]:
    """将过长原文拆成可独立翻译的小单元，优先沿原换行和句末标点切分。"""
    text = re.sub(r"[ \t\r]+", "", text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        remaining = paragraph
        while len(remaining) > max_chars:
            boundary = max(
                (remaining.rfind(mark, 80, max_chars + 1) for mark in "。！？；："),
                default=-1,
            )
            if boundary < max_chars // 3:
                boundary = max_chars
            else:
                boundary += 1
            units.append(remaining[:boundary].strip())
            remaining = remaining[boundary:].strip()
        if remaining:
            units.append(remaining)
    return units


def build_prompt(source_text: str, reference_text: str = "") -> str:
    reference_block = (
        f"\n已有现代译文参考（只用于理解语义，不要输出参考中的段号或说明）：\n{reference_text}\n"
        if reference_text else ""
    )
    return f"""你是佛典古汉语翻译员。只翻译下面这一小段《大方广佛华严经》原文。

硬性要求：
1. 只处理这段文字，不补写上下文，不把后文合并进来。
2. 保留人物、佛菩萨名号、数量、否定、时态和因果关系；不要删掉专名列表。
3. 必须翻译成现代简体中文，重组为现代语序；不能只做繁体转简体，不能把文言句式原样保留。
4. “一時”应根据语境译为“当时/那时”，“始成正覺”应译出“刚刚成佛/刚证得正觉”的语义，不能只改成“始成正觉”。
5. 已有现代译文参考只用于校正语义和专名，不要把参考段落的边界带入本次输出。
6. 原文是偈颂时保留分行感；原文是散文时按语义分句。
7. 只输出 JSON，不要 Markdown，不要输出思考过程：
   {{"translation":"完整白话译文","notes":["确有必要的词义说明"],"uncertain_terms":["无法确定的词"]}}
8. translation 必须完整覆盖输入原文；没有不确定词时数组为空。

原文：
{source_text}
{reference_block}
"""


def parse_json(text: str) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("translation"), str):
        raise ValueError("JSON 缺少 translation 字符串")
    return value


def call_model(model: str, source_text: str, reference_text: str = "", timeout: int = 900) -> dict:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [{"role": "user", "content": build_prompt(source_text, reference_text)}],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 3000},
            "keep_alive": 0,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    content = body.get("message", {}).get("content", "")
    return parse_json(content)


def load_done() -> dict[tuple[int, int, int], dict]:
    if not OUTPUT_PATH.exists():
        return {}
    done = {}
    for line in OUTPUT_PATH.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            key = (row["original_segment_index"], row["unit_index"], row["max_chars"])
            # 同一键可能有失败重试记录；优先保留最近的成功译文。
            if key not in done or row.get("translation") or not done[key].get("translation"):
                done[key] = row
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
    return done


def load_reference() -> dict[int, list[str]]:
    """读取已完成候选对齐的洪启嵩译文，作为语义参考而非最终结果。"""
    references: dict[int, list[str]] = {}
    if not REFERENCE_PATH.exists():
        return references
    for line in REFERENCE_PATH.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            text = row.get("text", "").strip()
            for index in row.get("original_segment_indices", []):
                if text:
                    references.setdefault(int(index), []).append(text)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return references


def build_units(rows: list[dict], chapter: int | None, start_segment: int | None,
                max_chars: int, references: dict[int, list[str]] | None = None) -> list[dict]:
    references = references or {}
    selected = [
        row for row in rows
        if (chapter is None or row.get("chapter") == chapter)
        and (start_segment is None or row.get("segment_index", -1) >= start_segment)
        and row.get("layer") == "原文"
    ]
    units = []
    for row in selected:
        source_units = split_source_units(row["text"], max_chars)
        for unit_index, source_text in enumerate(source_units):
            units.append({
                "book": BOOK,
                "volume": row.get("volume"),
                "chapter": row.get("chapter"),
                "chapter_title": row.get("chapter_title"),
                "book_chapter_label": row.get("book_chapter_label"),
                "source_file": row.get("source_file"),
                "original_segment_index": row["segment_index"],
                "unit_index": unit_index,
                "unit_count": len(source_units),
                "source_text": source_text,
                # 极短的经首句不需要参考译文，避免模型为一句话生成多余解释。
                "reference_text": (
                    "\n".join(references.get(row["segment_index"], []))
                    if len(source_text) >= 30 else ""
                ),
                "max_chars": max_chars,
            })
    return units


def materialize(rows: list[dict]) -> None:
    """将候选写成原文中心页面可读取的现代释译层。

    模型产出永远是 candidate；confidence 不得标 high。
    """
    from numerology.corpus_quality import (
        REVIEW_CANDIDATE,
        STATUS_LABELS,
        build_provenance,
    )

    LAYER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LAYER_PATH.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({
                "book": BOOK,
                "layer": "现代释译",
                "confidence": "low",
                "review_status": REVIEW_CANDIDATE,
                "alignment_status": STATUS_LABELS[REVIEW_CANDIDATE],
                "alignment_method": "一原文单元一译文请求",
                "translation_source": f"本地模型（{row['model']}）",
                "marker": f"O{row['original_segment_index']} · T{row['unit_index'] + 1}/{row['unit_count']}",
                "volume": row.get("volume"),
                "chapter": row.get("chapter"),
                "chapter_title": row.get("chapter_title"),
                "book_chapter_label": row.get("book_chapter_label"),
                "source_file": row.get("source_file"),
                "source_text": row["source_text"],
                "reference_used": bool(row.get("reference_text")),
                "text": row["translation"],
                "segment_index": row["original_segment_index"],
                "original_segment_index": row["original_segment_index"],
                "original_segment_indices": [row["original_segment_index"]],
                "translation_unit_index": row["unit_index"],
                "prompt_version": row["prompt_version"],
                "model": row["model"],
                "provenance": build_provenance(
                    pipeline="translate_huayan_segments",
                    model=row["model"],
                    prompt_version=row["prompt_version"],
                    source=f"本地模型（{row['model']}）",
                    extra={
                        "unit_index": row["unit_index"],
                        "unit_count": row["unit_count"],
                        "max_chars": row.get("max_chars"),
                    },
                ),
            }, ensure_ascii=False) + "\n")


def list_missing(max_chars: int = 700) -> dict:
    """统计尚未成功翻译的原文单元。

    同时报告：
    - translation_candidates 断点缺口（本脚本补跑入口）
    - generated_layers 已物化覆盖（可能来自历史批量层）
    """
    originals = load_originals()
    units = build_units(originals, None, None, max_chars, {})
    done = load_done()
    missing = []
    for unit in units:
        key = (unit["original_segment_index"], unit["unit_index"], unit["max_chars"])
        row = done.get(key)
        if not row or not row.get("translation"):
            missing.append({
                "original_segment_index": unit["original_segment_index"],
                "unit_index": unit["unit_index"],
                "volume": unit.get("volume"),
                "chapter": unit.get("chapter"),
                "chars": len(unit["source_text"]),
                "preview": unit["source_text"][:40],
            })
    by_volume: dict[int, int] = {}
    for item in missing:
        vol = int(item.get("volume") or 0)
        by_volume[vol] = by_volume.get(vol, 0) + 1

    generated_covered: set[int] = set()
    if LAYER_PATH.exists() and LAYER_PATH.stat().st_size:
        for line in LAYER_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("original_segment_indices"):
                generated_covered.update(int(i) for i in row["original_segment_indices"])
            elif row.get("original_segment_index") is not None:
                generated_covered.add(int(row["original_segment_index"]))
            elif row.get("segment_index") is not None:
                generated_covered.add(int(row["segment_index"]))
    original_ids = {
        int(row["segment_index"])
        for row in originals
        if row.get("segment_index") is not None
    }
    layer_missing = sorted(original_ids - generated_covered)
    return {
        "total_units": len(units),
        "candidate_missing_units": len(missing),
        "candidate_done_with_translation": sum(
            1 for row in done.values() if row.get("translation")
        ),
        "missing_by_volume": dict(sorted(by_volume.items())[:20]),
        "missing_sample": missing[:20],
        "generated_layers": {
            "path": str(LAYER_PATH),
            "original_segments": len(original_ids),
            "covered": len(generated_covered & original_ids),
            "missing": len(layer_missing),
            "missing_sample": layer_missing[:20],
        },
        "hint": (
            "补跑候选：--only-missing --limit N --materialize；"
            "generated_layers 覆盖缺口才是阅读页缺译的直接来源"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:30b-a3b")
    parser.add_argument("--chapter", type=int)
    parser.add_argument("--start-segment", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-chars", type=int, default=700)
    parser.add_argument("--materialize", action="store_true")
    parser.add_argument("--retry", action="store_true", help="重做已有同参数结果")
    parser.add_argument("--only-missing", action="store_true",
                        help="只处理尚无成功译文的单元（默认即跳过已完成；此开关打印缺段摘要）")
    parser.add_argument("--list-missing", action="store_true",
                        help="只列出缺段统计，不调用模型")
    args = parser.parse_args()
    if args.max_chars < 100:
        raise SystemExit("--max-chars 不能小于 100")

    if args.list_missing:
        print(json.dumps(list_missing(args.max_chars), ensure_ascii=False, indent=2))
        return

    units = build_units(
        load_originals(), args.chapter, args.start_segment, args.max_chars,
        load_reference(),
    )
    done = load_done()
    if args.only_missing:
        units = [
            unit for unit in units
            if not done.get(
                (unit["original_segment_index"], unit["unit_index"], unit["max_chars"]),
                {},
            ).get("translation")
        ]
        print(json.dumps({
            "only_missing": True, "pending_units": len(units),
        }, ensure_ascii=False))
    if args.limit:
        units = units[:args.limit]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    results = dict(done)
    new_count = 0
    with OUTPUT_PATH.open("a", encoding="utf-8") as handle:
        for unit in units:
            key = (unit["original_segment_index"], unit["unit_index"], unit["max_chars"])
            if key in done and not args.retry and done[key].get("translation"):
                continue
            started = time.time()
            try:
                result = call_model(
                    args.model, unit["source_text"], unit.get("reference_text", "")
                )
                row = {
                    **unit,
                    "translation": result["translation"].strip(),
                    "notes": result.get("notes", []),
                    "uncertain_terms": result.get("uncertain_terms", []),
                    "model": args.model,
                    "prompt_version": PROMPT_VERSION,
                    "status": "待人工复核",
                    "seconds": round(time.time() - started, 1),
                }
            except Exception as exc:  # 单段失败不影响断点续跑
                row = {
                    **unit,
                    "translation": "",
                    "model": args.model,
                    "prompt_version": PROMPT_VERSION,
                    "status": "失败",
                    "error": repr(exc),
                    "seconds": round(time.time() - started, 1),
                }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            # 重试失败时保留上一次成功结果，不能因为一次模型异常让页面译文消失。
            results[key] = (
                done[key] if row["status"] == "失败" and done.get(key, {}).get("translation")
                else row
            )
            new_count += 1
            print(json.dumps({
                "segment": unit["original_segment_index"],
                "unit": unit["unit_index"],
                "status": row["status"],
                "seconds": row["seconds"],
            }, ensure_ascii=False))
    if args.materialize:
        materialize([row for row in results.values() if row.get("translation")])
    print(json.dumps({"requested": len(units), "new": new_count,
                      "output": str(OUTPUT_PATH), "materialized": args.materialize}, ensure_ascii=False))


if __name__ == "__main__":
    main()
