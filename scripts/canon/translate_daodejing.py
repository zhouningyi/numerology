#!/usr/bin/env python3
"""逐章生成《道德经》王弼本的现代释译与可回查主题标注。

译文和标注均为本地模型候选，必须人工复核；主题 evidence 会逐字回查原文。
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


BOOK = "daodejing_wangbi"
ORIGINAL_PATH = Path(f"data/processed/canon/layers/{BOOK}_layers.jsonl")
OUTPUT_PATH = Path(f"data/processed/canon/translation_candidates/{BOOK}_translations.jsonl")
LAYER_PATH = Path(f"data/processed/canon/layers/{BOOK}_generated_layers.jsonl")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
PROMPT_VERSION = "daodejing-chapter-translation-v1"
ALLOWED_TAGS = ("道", "德", "无为", "自然", "反", "柔弱", "治国", "修身", "知足", "战争", "名与言", "生死", "玄", "祸福", "圣人")


def load_originals() -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in ORIGINAL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("layer") == "原文"
    ]


def build_prompt(source_text: str) -> str:
    tags = "、".join(ALLOWED_TAGS)
    return f"""你是严谨的先秦古汉语译者。请只处理下列《道德经》王弼本的一章原文。

要求：
1. 把整章译成自然、准确的现代简体中文；不要增添原文没有的观点，也不要引用王弼或其他注家。
2. 仅在原文确有明确依据时给 0–3 个主题标注；tag 必须从：{tags} 中选择。
3. 每个 evidence 必须是原文中连续、逐字一致的短语（2–20 字）；note 简短解释它为什么支持该标签。
4. 不把后世哲学术语或流行解读当作原文事实；没有可靠标签时 annotations 为空数组。
5. 只输出 JSON，不要 Markdown 或思考过程：
{{"translation":"完整白话译文","annotations":[{{"tag":"标签","evidence":"原文短语","note":"简短说明"}}],"notes":["必要的词义说明"],"uncertain_terms":["无法确定的词"]}}

原文：
{source_text}
"""


def parse_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("translation"), str):
        raise ValueError("JSON 缺少 translation 字符串")
    return value


def validate_annotations(value: object, source_text: str) -> list[dict[str, str]]:
    """仅保留限定标签、证据真实落在原文的标注。"""
    if not isinstance(value, list):
        return []
    valid: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        note = str(item.get("note", "")).strip()
        key = (tag, evidence)
        if tag in ALLOWED_TAGS and 2 <= len(evidence) <= 20 and evidence in source_text and key not in seen:
            valid.append({"tag": tag, "evidence": evidence, "note": note[:120]})
            seen.add(key)
    return valid[:3]


def call_model(model: str, source_text: str, timeout: int = 900) -> dict[str, Any]:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [{"role": "user", "content": build_prompt(source_text)}],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 1800},
            "keep_alive": 0,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_json(response.json().get("message", {}).get("content", ""))


def load_done() -> dict[int, dict[str, Any]]:
    if not OUTPUT_PATH.exists():
        return {}
    done: dict[int, dict[str, Any]] = {}
    for line in OUTPUT_PATH.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            index = int(row["original_segment_index"])
            if index not in done or row.get("translation"):
                done[index] = row
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return done


def layer_row(row: dict[str, Any]) -> dict[str, Any]:
    from numerology.corpus_quality import REVIEW_CANDIDATE, STATUS_LABELS, build_provenance

    return {
        "book": BOOK, "layer": "现代释译", "confidence": "low",
        "review_status": REVIEW_CANDIDATE, "alignment_status": STATUS_LABELS[REVIEW_CANDIDATE],
        "alignment_method": "一章一译文请求；主题证据逐字回查原文",
        "translation_source": f"本地模型（{row['model']}）", "marker": f"王弼本 · 第{row['chapter']}章",
        "volume": row.get("volume"), "chapter": row["chapter"], "chapter_title": row.get("chapter_title"),
        "book_chapter_label": row.get("book_chapter_label"), "source_file": row.get("source_file"),
        "source_text": row["source_text"], "text": row["translation"],
        "segment_index": row["original_segment_index"], "original_segment_index": row["original_segment_index"],
        "original_segment_indices": [row["original_segment_index"]], "translation_unit_index": 0,
        "annotations": row.get("annotations", []), "notes": row.get("notes", []),
        "uncertain_terms": row.get("uncertain_terms", []), "prompt_version": row["prompt_version"], "model": row["model"],
        "provenance": build_provenance(
            pipeline="translate_daodejing", model=row["model"], prompt_version=row["prompt_version"],
            source=f"本地模型（{row['model']}）", extra={"annotation_evidence_checked": True},
        ),
    }


def materialize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    LAYER_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged: dict[int, dict[str, Any]] = {}
    if LAYER_PATH.exists():
        for line in LAYER_PATH.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                merged[int(item.get("original_segment_index", item.get("segment_index")))] = item
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    for row in rows:
        if row.get("translation"):
            merged[int(row["original_segment_index"])] = layer_row(row)
    ordered = [merged[index] for index in sorted(merged)]
    tmp = LAYER_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for item in ordered:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    tmp.replace(LAYER_PATH)
    return {"path": str(LAYER_PATH), "total": len(ordered)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:30b-a3b")
    parser.add_argument("--chapter", type=int, help="只处理指定章")
    parser.add_argument("--limit", type=int, help="最多处理 N 章")
    parser.add_argument("--retry", action="store_true", help="重做已有成功候选")
    parser.add_argument("--materialize", action="store_true", help="将成功候选写入阅读层")
    args = parser.parse_args()
    originals = load_originals()
    if args.chapter is not None:
        originals = [row for row in originals if row.get("chapter") == args.chapter]
    done = load_done()
    pending = [row for row in originals if args.retry or not done.get(int(row["segment_index"]), {}).get("translation")]
    if args.limit:
        pending = pending[:args.limit]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    results = dict(done)
    with OUTPUT_PATH.open("a", encoding="utf-8") as handle:
        for original in pending:
            index = int(original["segment_index"])
            started = time.time()
            base = {
                "book": BOOK, "chapter": original["chapter"], "chapter_title": original.get("chapter_title"),
                "book_chapter_label": original.get("book_chapter_label"), "volume": original.get("volume"),
                "source_file": original.get("source_file"), "original_segment_index": index,
                "source_text": original["text"], "model": args.model, "prompt_version": PROMPT_VERSION,
            }
            try:
                result = call_model(args.model, original["text"])
                row = {**base, "translation": result["translation"].strip(),
                       "annotations": validate_annotations(result.get("annotations"), original["text"]),
                       "notes": result.get("notes", []), "uncertain_terms": result.get("uncertain_terms", []),
                       "status": "待人工复核", "seconds": round(time.time() - started, 1)}
            except Exception as exc:
                row = {**base, "translation": "", "annotations": [], "status": "失败", "error": repr(exc),
                       "seconds": round(time.time() - started, 1)}
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            if row["translation"]:
                results[index] = row
            print(json.dumps({"chapter": original["chapter"], "status": row["status"], "annotations": len(row["annotations"]), "seconds": row["seconds"]}, ensure_ascii=False))
    info = materialize(list(results.values())) if args.materialize else None
    print(json.dumps({"requested": len(pending), "candidate_total": len(results), "materialize_info": info}, ensure_ascii=False))


if __name__ == "__main__":
    main()
