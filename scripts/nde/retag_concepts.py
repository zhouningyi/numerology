#!/usr/bin/env python3
"""概念标注重标（v2）：严格判据 + 逐字引用校验，纠正首轮过度标注。

首轮标注（translate_nderf.py 顺路做的）精确率不足：时间虚幻等概念被标到
半数以上案例，证据句常与概念无关。本脚本用带正反例的严格判据重标：
- 概念必须被体验者**明确表达**，拿不准一律不标（精确率优先）；
- 证据必须是叙述原文的逐字引用，本地校验非子串即丢弃（连同该标签）。

输出 data/processed/nderf/concepts_v2.jsonl（追加式，同 slug 最后一条为准）。
普通模式 gpt-4o-mini；--repair 用 gpt-4o 重做证据校验全军覆没的案例。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

from scripts.nde.translate_nderf import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EXPERIENCES = Path("data/processed/nderf/experiences.jsonl")
OUTPUT = Path("data/processed/nderf/concepts_v2.jsonl")

# 严格判据：每个概念给出判定标准与正反例（反例=首轮的典型误标）
SYSTEM_PROMPT = """你是严谨的文本标注员。阅读一篇濒死体验英文自述，判断其中是否**明确表达**了下列概念。
铁律：
1. 只有叙述者明确表达了该概念才标；氛围相似、可以引申的一律不标。宁缺勿滥。
2. 每个命中概念必须给出一句**逐字引用**的英文原句作为证据（原文连续片段，不得改写）。
3. 拿不准就不标。大部分文章命中 0-3 个概念是正常的。

概念与判据：
- scale_illusion 尺度虚幻：明确谈到大小/尺度失去意义、极大极小互换互含。
  正例 "size meant nothing there; the smallest thing contained the whole universe"
  反例 "the place was vast"（只是大，不是尺度虚幻）
- time_illusion 时间虚幻：明确谈到时间不存在/停止/非线性/同时看到过去未来。
  正例 "there was no time there" / "past and future existed at once"
  反例 "I felt peaceful"（与时间无关）；"it felt like hours"（只是时间估计）
- interpenetration 一即一切：明确表达部分包含整体、万物互相含摄映现。
  正例 "every point contained all of it"
  反例 "everything was connected"（这是 oneness，不是互摄）
- oneness 万物一体：明确表达自他界限消失、与万物同一。
  正例 "I was everything and everything was me"
  反例 "I felt close to God"（亲近不等于同一）
- consciousness_independent 意识独立于身体：明确**主张**意识不依赖大脑/身体存在。
  正例 "I knew then that consciousness does not need the brain"
  反例 "I floated above my body"（这是离体现象描述，不是主张）
- direct_knowing 直接知识：明确描述不经语言推理的整体性领悟/知识灌注。
  正例 "I suddenly knew everything, understanding poured into me without words"
  反例 "the being spoke to me"（有语言传递）
- light_conscious 光即存有：明确描述光本身有意识/人格/爱。
  正例 "the light itself was alive and loved me"
  反例 "I saw a bright light"（只是看到光）
- multiple_realms 层级世界：明确描述多重世界/维度/层级结构。
  正例 "I saw many levels of existence"
  反例 "I went to a beautiful place"（一个地方不是多层）
- love_fundamental 爱为基底：明确表达爱是宇宙的本质/构成/终极实在。
  正例 "love is the fabric of everything that exists"
  反例 "I felt loved"（感到被爱不是本体论主张）
- more_real 比现实更真实：明确将体验与日常清醒相比并断言更真实。
  正例 "it was more real than anything in this life"
  反例 "it was vivid"（清晰不等于更真实的比较）
- no_judgment 无评判的回顾：仅在生命回顾/评判语境中，明确表达评判来自自己而非外在审判者、或被全然接纳。
  正例 "no one judged me; I was the only one judging myself"
  反例 "I felt peace"（与评判无关）
- purpose_order 万物有序：明确表达一切事件有意义/有安排/互相关联成整体。
  正例 "I saw that every event in my life had a purpose and fit together"
  反例 "I was told to go back"（个别指令不是整体秩序）

只输出 JSON：{"概念key": "逐字引用的英文证据句", ...}；没有命中输出 {}。"""


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


def _norm(text: str) -> str:
    return re.sub(r"[\s ]+", " ", text).lower()


def verify_quotes(description: str, tags: dict) -> dict[str, str]:
    """证据必须是叙述的逐字子串（空白归一化后比较）。"""
    haystack = _norm(description)
    verified = {}
    for key, quote in tags.items():
        if not isinstance(quote, str):
            continue
        needle = _norm(quote.strip().strip('"'))
        if len(needle) >= 12 and needle in haystack:
            verified[key] = quote.strip().strip('"')
    return verified


def _tag_one(client, model: str, effort: str | None, record: dict) -> dict:
    kwargs = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": record["description"][:12000]},
        ],
    }
    if model.startswith("gpt-5"):
        if effort:
            kwargs["reasoning_effort"] = effort
    else:
        kwargs["temperature"] = 0
    response = client.chat.completions.create(**kwargs)
    raw = json.loads(response.choices[0].message.content)
    return verify_quotes(record["description"], raw)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--reasoning-effort", default="low",
                        help="gpt-5 系列的推理档位（low 校准与默认档一致且快 3 倍）")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--repair", action="store_true",
                        help="重做证据校验为空但首轮有概念的案例（升级 gpt-5）")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-chars", type=int, default=200)
    args = parser.parse_args()
    model = args.model if not args.repair else (
        args.model if args.model != "gpt-5-mini" else "gpt-5"
    )

    load_dotenv()
    with EXPERIENCES.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    done = load_done()
    todo = [
        r for r in records
        if r["slug"] not in done and len(r.get("description", "")) >= args.min_chars
    ]
    if args.limit:
        todo = todo[: args.limit]
    est_tokens = sum(len(r["description"]) for r in todo) / 4 + len(todo) * 900
    price_in = 1.25 if model == "gpt-5" else 0.25
    logger.info(f"模型={model} 档位={args.reasoning_effort} 并发={args.workers} "
                f"待重标 {len(todo)} 篇；预估输入费用约 ${est_tokens/1e6*price_in:.2f}")
    if args.dry_run:
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import Lock

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    count = fail = 0
    lock = Lock()
    with OUTPUT.open("a", encoding="utf-8") as out, ThreadPoolExecutor(
        max_workers=args.workers
    ) as pool:
        futures = {
            pool.submit(_tag_one, client, model, args.reasoning_effort, r): r
            for r in todo
        }
        for future in as_completed(futures):
            record = futures[future]
            try:
                verified = future.result()
                with lock:
                    out.write(json.dumps(
                        {"slug": record["slug"], "concepts": verified, "model": model},
                        ensure_ascii=False,
                    ) + "\n")
                    count += 1
                    if count % 200 == 0:
                        out.flush()
                        logger.info(f"进度 {count}/{len(todo)}（失败 {fail}）")
            except Exception as exc:  # noqa: BLE001
                with lock:
                    fail += 1
                logger.warning(f"{record['slug']} 失败: {str(exc)[:120]}")
    logger.info(f"完成：重标 {count} 篇，失败 {fail} -> {OUTPUT}")


if __name__ == "__main__":
    main()
