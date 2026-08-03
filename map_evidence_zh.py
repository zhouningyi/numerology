#!/usr/bin/env python3
"""把概念证据句映射到中文译文（用于在译文上做高亮圈注）。

概念标注时的证据句是英文原句；本脚本对每篇已翻译案例做一次小调用：
给出中文译文与英文证据句，要求返回译文中对应的连续原句子串。
输出 data/processed/nderf/evidence_zh.jsonl（按 slug 增量，断点续跑）。

用法:
    python3 map_evidence_zh.py --dry-run
    python3 map_evidence_zh.py --limit 20
    python3 map_evidence_zh.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

from translate_nderf import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TRANSLATIONS = Path("data/processed/nderf/translations.jsonl")
OUTPUT = Path("data/processed/nderf/evidence_zh.jsonl")

SYSTEM_PROMPT = """给你一篇中文译文和若干英文证据句（键为概念名）。
对每个概念，在中文译文里找出与英文证据句对应的那句话，返回译文中的**连续原文子串**
（必须逐字取自译文，不要改写、不要截半句）。找不到对应句子的概念省略。
只输出 JSON：{"概念key": "译文中的句子", ...}"""


def load_done() -> set[str]:
    if not OUTPUT.exists():
        return set()
    done = set()
    with OUTPUT.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                done.add(json.loads(line)["slug"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    with TRANSLATIONS.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    done = load_done()
    todo = [
        r for r in records
        if r["slug"] not in done and r.get("zh") and r.get("concepts")
    ]
    if args.limit:
        todo = todo[: args.limit]
    est_tokens = sum(len(r["zh"]) + 200 for r in todo) / 3
    logger.info(
        f"已完成 {len(done)}，待映射 {len(todo)} 篇；"
        f"预估费用约 ${est_tokens/1e6*0.15 + est_tokens*0.15/1e6*0.6:.2f}"
    )
    if args.dry_run:
        return

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    count = fail = 0
    with OUTPUT.open("a", encoding="utf-8") as out:
        for record in todo:
            payload_in = (
                "中文译文：\n" + record["zh"] +
                "\n\n英文证据句：\n" + json.dumps(record["concepts"], ensure_ascii=False)
            )
            try:
                response = client.chat.completions.create(
                    model=args.model,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": payload_in},
                    ],
                    temperature=0,
                )
                mapping = json.loads(response.choices[0].message.content)
                # 只保留确实是译文子串的句子，防止模型改写
                verified = {
                    key: sentence for key, sentence in mapping.items()
                    if isinstance(sentence, str) and len(sentence) >= 4
                    and sentence in record["zh"]
                }
                out.write(json.dumps(
                    {"slug": record["slug"], "concepts_zh": verified,
                     "model": args.model},
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
