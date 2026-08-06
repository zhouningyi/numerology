#!/usr/bin/env python3
"""为《道德经》项目自译生成逐句对应，供原文悬停显示对应白话。"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from numerology.huayan_sentence_align import split_classic_sentences


BOOK = "daodejing_wangbi"
LAYER_PATH = Path(f"data/processed/canon/layers/{BOOK}_generated_layers.jsonl")
OUTPUT_PATH = Path(f"data/processed/canon/translation_candidates/{BOOK}_sentence_pairs.jsonl")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
PROMPT_VERSION = "daodejing-sentence-pairs-v1"


def build_prompt(sentences: list[str], chapter_translation: str) -> str:
    source = json.dumps(sentences, ensure_ascii=False)
    return f"""你是严谨的先秦古汉语译者。下面是《道德经》王弼本同一章已严格切分的原文句子。

逐条翻译：数组中的第 N 项必须只翻译第 N 句。不要合并、拆分、遗漏、改写原句或增加解释。
已生成的整章译文仅用于保持术语一致，不必照抄。

只输出 JSON：{{"translations":["第1句的现代汉语", "第2句的现代汉语"]}}。
translations 的元素数必须与原文数组完全相同，每一项必须是完整自然的简体中文。

原文句子：
{source}

整章译文参考：
{chapter_translation}
"""


def parse_translations(text: str, expected: int) -> list[str]:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON")
    value = json.loads(cleaned[start:end + 1])
    translations = value.get("translations") if isinstance(value, dict) else None
    if not isinstance(translations, list) or len(translations) != expected:
        raise ValueError(f"逐句译文数量不匹配：期望 {expected}")
    result = [str(item).strip() for item in translations]
    if any(not item for item in result):
        raise ValueError("逐句译文含空项")
    return result


def call_model(model: str, sentences: list[str], chapter_translation: str) -> list[str]:
    schema = {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array", "minItems": len(sentences), "maxItems": len(sentences),
                "items": {"type": "string"},
            },
        },
        "required": ["translations"],
    }
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [{"role": "user", "content": build_prompt(sentences, chapter_translation)}],
            # JSON Schema 强制数组长度，避免模型把相邻原句并成一条译文。
            "stream": False, "format": schema, "think": False,
            "options": {"temperature": 0.05, "num_predict": 1800}, "keep_alive": 0,
        }, timeout=900,
    )
    response.raise_for_status()
    return parse_translations(response.json().get("message", {}).get("content", ""), len(sentences))


def call_single_sentence(model: str, sentence: str) -> str:
    """长章的 JSON 被截断时，以单句请求回退，宁慢勿错位。"""
    schema = {
        "type": "object", "properties": {"translation": {"type": "string"}},
        "required": ["translation"],
    }
    prompt = (
        "将下面《道德经》原句准确译成一条自然的现代简体中文。"
        "只输出 JSON：{\"translation\":\"译文\"}，不要解释或合并别句。\n原句：\n"
        f"{sentence}"
    )
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "format": schema, "think": False,
            "options": {"temperature": 0.05, "num_predict": 450}, "keep_alive": 0,
        }, timeout=900,
    )
    response.raise_for_status()
    value = json.loads(response.json().get("message", {}).get("content", ""))
    translation = value.get("translation") if isinstance(value, dict) else None
    if not isinstance(translation, str) or not translation.strip():
        raise ValueError("单句译文为空")
    return translation.strip()


def validate_pairs(source_text: str, pairs: object) -> list[list[str]]:
    """仅接受原文切句逐字不变、数量完整的一对一句对。"""
    sentences = split_classic_sentences(source_text)
    if not isinstance(pairs, list) or len(pairs) != len(sentences):
        return []
    cleaned: list[list[str]] = []
    for original, pair in zip(sentences, pairs):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return []
        if pair[0] != original or not isinstance(pair[1], str) or not pair[1].strip():
            return []
        cleaned.append([original, pair[1].strip()])
    return cleaned


def load_rows() -> list[dict[str, Any]]:
    return [json.loads(line) for line in LAYER_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_done() -> dict[int, dict[str, Any]]:
    if not OUTPUT_PATH.exists():
        return {}
    done: dict[int, dict[str, Any]] = {}
    for line in OUTPUT_PATH.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            if item.get("pairs"):
                done[int(item["original_segment_index"])] = item
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return done


def materialize(pair_rows: dict[int, dict[str, Any]]) -> int:
    rows = load_rows()
    count = 0
    for row in rows:
        index = row.get("original_segment_index")
        candidate = pair_rows.get(index)
        if not candidate:
            continue
        pairs = validate_pairs(row.get("source_text", ""), candidate.get("pairs"))
        if not pairs:
            continue
        row["chapter_translation"] = row.get("text", "")
        row["pairs"] = pairs
        row["pair_sources"] = ["ai"] * len(pairs)
        row["marker"] = f"王弼本 · 第{row.get('chapter')}章 · {len(pairs)} 句对译"
        row["alignment_method"] = "逐句独立翻译；原文切句与数量严格校验"
        row["prompt_version"] = PROMPT_VERSION
        count += 1
    tmp = LAYER_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(LAYER_PATH)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--chapter", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    rows = load_rows()
    done = load_done()
    pending = [
        row for row in rows
        if (args.chapter is None or row.get("chapter") == args.chapter)
        and (args.retry or int(row["original_segment_index"]) not in done)
    ]
    if args.limit:
        pending = pending[:args.limit]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    results = dict(done)
    with OUTPUT_PATH.open("a", encoding="utf-8") as handle:
        for row in pending:
            index = int(row["original_segment_index"])
            sentences = split_classic_sentences(row["source_text"])
            started = time.time()
            try:
                try:
                    translations = call_model(args.model, sentences, row.get("chapter_translation") or row["text"])
                    method = "整章结构化逐句翻译"
                except (ValueError, json.JSONDecodeError):
                    translations = [call_single_sentence(args.model, sentence) for sentence in sentences]
                    method = "单句回退翻译（整章 JSON 输出失败）"
                candidate = {
                    "book": BOOK, "chapter": row.get("chapter"), "original_segment_index": index,
                    "pairs": [[source, translation] for source, translation in zip(sentences, translations)],
                    "model": args.model, "prompt_version": PROMPT_VERSION,
                    "status": "待人工复核", "method": method, "seconds": round(time.time() - started, 1),
                }
            except Exception as exc:
                candidate = {"book": BOOK, "chapter": row.get("chapter"), "original_segment_index": index,
                             "pairs": [], "model": args.model, "prompt_version": PROMPT_VERSION,
                             "status": "失败", "error": repr(exc), "seconds": round(time.time() - started, 1)}
            handle.write(json.dumps(candidate, ensure_ascii=False) + "\n")
            handle.flush()
            if candidate["pairs"]:
                results[index] = candidate
            print(json.dumps({"chapter": row.get("chapter"), "sentences": len(sentences), "status": candidate["status"], "seconds": candidate["seconds"]}, ensure_ascii=False))
    print(json.dumps({"requested": len(pending), "materialized": materialize(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
