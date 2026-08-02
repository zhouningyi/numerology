#!/usr/bin/env python3
"""NDERF 案例翻译 + 概念标注（gpt-4o-mini，一次调用两件事）。

对每篇案例的叙述正文：
  1. 翻译为中文；
  2. 按 numerology/nde/concepts.yaml 的概念体系打标签，并摘出原文证据句。

输出 data/processed/nderf/translations.jsonl（按 slug 增量，断点续跑，
与 experiences.jsonl 分离——重跑解析不会冲掉翻译）。

用法:
    export OPENAI_API_KEY=sk-...
    python3 translate_nderf.py --dry-run          # 只统计篇数与预估费用
    python3 translate_nderf.py --limit 10         # 试译 10 篇看质量
    python3 translate_nderf.py                    # 全量（增量续跑）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EXPERIENCES = Path("data/processed/nderf/experiences.jsonl")
OUTPUT = Path("data/processed/nderf/translations.jsonl")
CONCEPTS_PATH = Path("numerology/nde/concepts.yaml")

# gpt-4o-mini 定价（USD / 1M tokens）
PRICE_IN, PRICE_OUT = 0.15, 0.60


def build_system_prompt() -> str:
    concepts = yaml.safe_load(CONCEPTS_PATH.read_text(encoding="utf-8"))["concepts"]
    lines = "\n".join(
        f"- {key}: {spec['name']} —— {spec['description']}"
        for key, spec in concepts.items()
    )
    return f"""你是研究助理，处理一篇濒死体验（NDE）自述。完成两件事：

1. 把英文叙述完整翻译成自然流畅的中文（不要逐字直译，保留第一人称口吻）。
2. 概念标注：判断叙述中是否明确表达了下列概念，只标确实出现的，宁缺勿滥；
   每个命中概念摘录一句最能体现它的英文原句。

概念清单：
{lines}

只输出 JSON：{{"zh": "中文翻译", "concepts": {{"概念key": "英文证据句", ...}}}}
没有命中任何概念时 concepts 为空对象。"""


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
    parser.add_argument("--min-chars", type=int, default=200,
                        help="叙述过短（表单噪声）的跳过阈值")
    args = parser.parse_args()

    with EXPERIENCES.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    done = load_done()
    todo = [
        r for r in records
        if r["slug"] not in done and len(r.get("description", "")) >= args.min_chars
    ]
    if args.limit:
        todo = todo[: args.limit]

    est_tokens = sum(len(r["description"]) for r in todo) / 4  # 粗估
    est_cost = est_tokens / 1e6 * PRICE_IN + est_tokens * 1.1 / 1e6 * PRICE_OUT
    logger.info(
        f"已完成 {len(done)}，待处理 {len(todo)} 篇；"
        f"预估 {est_tokens/1e6:.1f}M 输入 tokens，费用约 ${est_cost:.2f}（{args.model}）"
    )
    if args.dry_run:
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("请先 export OPENAI_API_KEY=...")
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    system_prompt = build_system_prompt()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    count = fail = 0
    with OUTPUT.open("a", encoding="utf-8") as out:
        for record in todo:
            try:
                response = client.chat.completions.create(
                    model=args.model,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": record["description"]},
                    ],
                    temperature=0.2,
                )
                payload = json.loads(response.choices[0].message.content)
                out.write(json.dumps({
                    "slug": record["slug"],
                    "zh": payload.get("zh", ""),
                    "concepts": payload.get("concepts", {}),
                    "model": args.model,
                    "translated_at": time.strftime("%Y-%m-%d"),
                }, ensure_ascii=False) + "\n")
                count += 1
                if count % 50 == 0:
                    out.flush()
                    logger.info(f"进度 {count}/{len(todo)}（失败 {fail}）")
            except Exception as exc:  # noqa: BLE001 —— 单篇失败不中断批量
                fail += 1
                logger.warning(f"{record['slug']} 失败: {exc}")
                time.sleep(2)
    logger.info(f"完成：新翻译 {count} 篇，失败 {fail} 篇 -> {OUTPUT}")


if __name__ == "__main__":
    main()
