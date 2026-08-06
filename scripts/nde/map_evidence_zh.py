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

from scripts.nde.translate_nderf import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TRANSLATIONS = Path("data/processed/nderf/translations.jsonl")
CONCEPTS_V2 = Path("data/processed/nderf/concepts_v2.jsonl")
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


def map_one(client, model: str, effort: str | None, record: dict) -> dict[str, str]:
    sentences = split_sentences(record["zh"])
    if not sentences:
        return {}
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    payload = (
        "中文句子列表：\n" + numbered +
        "\n\n英文证据句：\n" + json.dumps(record["concepts"], ensure_ascii=False)
    )
    kwargs = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ],
    }
    if model.startswith("gpt-5"):
        if effort:
            kwargs["reasoning_effort"] = effort
    else:
        kwargs["temperature"] = 0
    response = client.chat.completions.create(**kwargs)
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


def compact_evidence_file() -> dict:
    """把追加式 evidence_zh 压成每 slug 一条（保留最后一次）。"""
    mapped = load_mapped()
    if not OUTPUT.exists() and not mapped:
        return {"slugs": 0, "written": False}
    bak = OUTPUT.with_suffix(".jsonl.bak")
    if OUTPUT.exists():
        import shutil
        shutil.copy2(OUTPUT, bak)
    with OUTPUT.open("w", encoding="utf-8") as handle:
        for slug, concepts_zh in sorted(mapped.items()):
            handle.write(json.dumps(
                {"slug": slug, "concepts_zh": concepts_zh, "compacted": True},
                ensure_ascii=False,
            ) + "\n")
    return {"slugs": len(mapped), "written": True, "backup": str(bak)}


def build_todo(records: list[dict], mapped: dict[str, dict], mode: str) -> list[dict]:
    """mode: missing | incomplete | all_gaps | repair(legacy=incomplete)。"""
    todo = []
    for record in records:
        if not record.get("zh") or not record.get("concepts"):
            continue
        slug = record["slug"]
        have = mapped.get(slug)
        n_concepts = len(record["concepts"])
        if mode == "missing":
            if have is None:
                todo.append(record)
        elif mode in {"incomplete", "repair"}:
            if have is not None and len(have) < n_concepts:
                todo.append(record)
        else:  # all_gaps
            if have is None or len(have) < n_concepts:
                todo.append(record)
    return todo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--reasoning-effort", default="minimal",
                        help="选句任务较简单，minimal 档足够且最快")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--repair", action="store_true",
                        help="重做圈注数少于概念数的案例（升级 gpt-5）")
    parser.add_argument(
        "--mode",
        choices=["missing", "incomplete", "all_gaps", "repair"],
        default=None,
        help="missing=完全无中文圈注；incomplete=键不全；all_gaps=二者；默认无参=missing",
    )
    parser.add_argument("--compact", action="store_true",
                        help="仅压实 evidence_zh.jsonl（每 slug 保留最后一条）")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.compact:
        print(json.dumps(compact_evidence_file(), ensure_ascii=False))
        return

    mode = args.mode
    if mode is None:
        mode = "repair" if args.repair else "missing"
    model = args.model
    if mode in {"incomplete", "repair", "all_gaps"} and args.model == "gpt-5-mini":
        # 补缺/修复默认升一档，可用 --model 覆盖
        if args.repair or mode in {"incomplete", "all_gaps"}:
            model = "gpt-5" if args.repair else args.model

    load_dotenv()
    with TRANSLATIONS.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    # 概念以 v2 严格重标为准
    concepts_v2 = {}
    if CONCEPTS_V2.exists():
        with CONCEPTS_V2.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                concepts_v2[row["slug"]] = row.get("concepts", {})
    for record in records:
        if record["slug"] in concepts_v2:
            record["concepts"] = concepts_v2[record["slug"]]

    mapped = load_mapped()
    todo = build_todo(records, mapped, mode)
    if args.limit:
        todo = todo[: args.limit]
    logger.info(
        f"模式={mode} 模型={model} 档位={args.reasoning_effort} "
        f"并发={args.workers} 待处理 {len(todo)} 篇 "
        f"(已有圈注 slug={len(mapped)})"
    )
    if args.dry_run:
        print(json.dumps({
            "mode": mode, "todo": len(todo), "mapped_slugs": len(mapped),
        }, ensure_ascii=False))
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
            pool.submit(map_one, client, model, args.reasoning_effort, r): r
            for r in todo
        }
        for future in as_completed(futures):
            record = futures[future]
            try:
                concepts_zh = future.result()
                with lock:
                    out.write(json.dumps(
                        {"slug": record["slug"], "concepts_zh": concepts_zh,
                         "model": model, "mode": mode},
                        ensure_ascii=False,
                    ) + "\n")
                    count += 1
                    if count % 200 == 0:
                        out.flush()
                        logger.info(f"进度 {count}/{len(todo)}（失败 {fail}）")
            except Exception as exc:  # noqa: BLE001 —— 单篇失败不中断批量
                with lock:
                    fail += 1
                logger.warning(f"{record['slug']} 失败: {str(exc)[:120]}")
    logger.info(f"完成：映射 {count} 篇，失败 {fail} -> {OUTPUT}")


if __name__ == "__main__":
    main()
