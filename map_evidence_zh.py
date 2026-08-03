#!/usr/bin/env python3
"""把概念证据句映射到中文译文（用于在译文上做高亮圈注）。

方法：把译文拆句并编号，让模型为每个概念**选句子编号**（找不到完全对应时
必须选意思最接近的一句）——命中句子由构造保证是译文原文，杜绝改写丢失。
凡是标了概念的文章，中文至少圈出一处重点。

两种模式：
    普通模式  处理尚未映射的案例（默认 gpt-4o-mini）
    --repair  重做"圈注数 < 概念数"的案例（默认升级 gpt-4o，高级模型兜底）

输出 data/processed/nderf/evidence_zh.jsonl（追加式，同 slug 以最后一条为准）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

from translate_nderf import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TRANSLATIONS = Path("data/processed/nderf/translations.jsonl")
OUTPUT = Path("data/processed/nderf/evidence_zh.jsonl")

SYSTEM_PROMPT = """下面给你一篇濒死体验中文译文的编号句子列表，以及若干英文证据句（键为概念）。
任务：为每个概念选出最能对应英文证据句的中文句子编号。

规则：
- 每个概念必须给出编号（1 个，语义跨两句时可给 2 个连续编号）；
- 找不到完全对应时，选择意思最接近的一句，不允许省略概念；
- 只输出 JSON：{"概念key": [编号], ...}"""

_SENT_RE = re.compile(r"[^。！？；\n]+[。！？；]?")


def split_sentences(zh: str) -> list[str]:
    sentences = [s.strip() for s in _SENT_RE.findall(zh)]
    return [s for s in sentences if len(s) >= 2]


def load_mapped() -> dict[str, dict]:
    """同 slug 追加多次时，以最后一条为准（repair 覆盖旧结果）。"""
    mapped: dict[str, dict] = {}
    if not OUTPUT.exists():
        return mapped
    with OUTPUT.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
                mapped[row["slug"]] = row.get("concepts_zh", {})
            except (json.JSONDecodeError, KeyError):
                continue
    return mapped


def map_one(client, model: str, record: dict) -> dict[str, str]:
    sentences = split_sentences(record["zh"])
    if not sentences:
        return {}
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    payload = (
        "中文句子列表：\n" + numbered +
        "\n\n英文证据句：\n" + json.dumps(record["concepts"], ensure_ascii=False)
    )
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ],
        temperature=0,
    )
    raw = json.loads(response.choices[0].message.content)
    result: dict[str, str] = {}
    for key in record["concepts"]:
        indexes = raw.get(key)
        if isinstance(indexes, int):
            indexes = [indexes]
        if not isinstance(indexes, list):
            continue
        picked = [
            sentences[i - 1]
            for i in indexes[:2]
            if isinstance(i, int) and 1 <= i <= len(sentences)
        ]
        if picked:
            result[key] = "".join(picked)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None,
                        help="缺省：普通模式 gpt-4o-mini，repair 模式 gpt-4o")
    parser.add_argument("--repair", action="store_true",
                        help="用高级模型重做圈注不全的案例")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    model = args.model or ("gpt-4o" if args.repair else "gpt-4o-mini")

    load_dotenv()
    with TRANSLATIONS.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    mapped = load_mapped()
    if args.repair:
        todo = [
            r for r in records
            if r.get("zh") and r.get("concepts")
            and len(mapped.get(r["slug"], {})) < len(r["concepts"])
        ]
    else:
        todo = [
            r for r in records
            if r["slug"] not in mapped and r.get("zh") and r.get("concepts")
        ]
    if args.limit:
        todo = todo[: args.limit]
    est_tokens = sum(len(r["zh"]) + 300 for r in todo) / 3
    price_in, price_out = (2.5, 10.0) if model.startswith("gpt-4o") and "mini" not in model else (0.15, 0.6)
    logger.info(
        f"模式={'repair' if args.repair else 'normal'} 模型={model} 待处理 {len(todo)} 篇；"
        f"预估费用约 ${est_tokens/1e6*price_in + est_tokens*0.1/1e6*price_out:.2f}"
    )
    if args.dry_run:
        return

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    count = fail = 0
    with OUTPUT.open("a", encoding="utf-8") as out:
        for record in todo:
            try:
                concepts_zh = map_one(client, model, record)
                out.write(json.dumps(
                    {"slug": record["slug"], "concepts_zh": concepts_zh,
                     "model": model},
                    ensure_ascii=False,
                ) + "\n")
                count += 1
                if count % 100 == 0:
                    out.flush()
                    logger.info(f"进度 {count}/{len(todo)}（失败 {fail}）")
            except Exception as exc:  # noqa: BLE001 —— 单篇失败不中断批量
                fail += 1
                logger.warning(f"{record['slug']} 失败: {exc}")
                time.sleep(2)
    logger.info(f"完成：映射 {count} 篇，失败 {fail} -> {OUTPUT}")


if __name__ == "__main__":
    main()
