#!/usr/bin/env python3
"""华严对齐结果的自动复核：模型逐句判定"原文↔译文是否对应"。

人工不可能读完 1.6 万句对，本脚本把确认自动化：
- 每个句对交给模型判定语义对应（批量、minimal 档，判断任务足够）；
- 空译文/超短译文本地直接判不通过；
- 全段通过 → 写复核记录 review_status=model_agree（进入"模型一致·待人工"）；
  有句不过 → 保持 candidate 并在备注列出不对应的句号（人工队列按此优先）。

复核结果按项目约定写 reviews/<book>_reviews.jsonl（unit_key 覆盖、
不改生成层、后写的人工结论覆盖模型结论）。幂等：已有人工/模型结论的单元跳过。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from numerology.corpus_quality import (  # noqa: E402
    REVIEW_CANDIDATE,
    REVIEW_MODEL_AGREE,
    utc_now_iso,
)
from numerology.corpus_review import (  # noqa: E402
    append_review,
    load_reviews,
    text_fingerprint,
    unit_key_from_row,
)
from scripts.nde.translate_nderf import load_dotenv  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LAYERS_DIR = Path("data/processed/canon/layers")

JUDGE_PROMPT = """给你若干编号的句对，每对是佛经原文句（古文）和一句现代译文。
逐对判断译文是否是该原文句的翻译（意思对应即可，允许意译与合并相邻语义）。
明显不对应（讲的不是同一件事、张冠李戴、空洞套话）判 false。
只输出 JSON：{"1": true, "2": false, ...}"""


def judge_pairs(client, model: str, pairs: list[list[str]]) -> list[bool]:
    numbered = "\n".join(
        f"{i + 1}. 原:{orig[:120]}\n   译:{(trans or '（空）')[:160]}"
        for i, (orig, trans) in enumerate(pairs)
    )
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        reasoning_effort="minimal",
        messages=[
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": numbered[:40000]},
        ],
    )
    raw = json.loads(response.choices[0].message.content)
    return [bool(raw.get(str(i + 1), False)) for i in range(len(pairs))]


def verify_row(client, model: str, row: dict) -> tuple[bool, list[int]]:
    """返回（是否全部通过, 未通过句号列表[1-based]）。"""
    pairs = row.get("pairs") or []
    failed: list[int] = []
    to_judge, judge_index = [], []
    for i, pair in enumerate(pairs):
        orig, trans = pair[0], pair[1]
        if not trans or len(trans.strip()) < 2:
            failed.append(i + 1)
        else:
            to_judge.append([orig, trans])
            judge_index.append(i)
    if to_judge:
        verdicts = judge_pairs(client, model, to_judge)
        for idx, ok in zip(judge_index, verdicts):
            if not ok:
                failed.append(idx + 1)
    return (not failed), sorted(failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default="huayan_t0279")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="重验已有模型结论的单元")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    path = LAYERS_DIR / f"{args.book}_generated_layers.jsonl"
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    rows = [r for r in rows if r.get("pairs") and r.get("layer") in ("现代释译", "现代白话")]
    reviews = load_reviews(args.book)
    todo = []
    for row in rows:
        key = unit_key_from_row(args.book, row)
        if not key:
            continue
        existing = reviews.get(key, {}).get("review_status")
        if existing == "human_verified" or existing == "rejected":
            continue  # 人工结论永不覆盖
        if existing == REVIEW_MODEL_AGREE and not args.force:
            continue
        if existing == REVIEW_CANDIDATE and reviews.get(key, {}).get("reviewer", "").startswith("model:") and not args.force:
            continue  # 模型已判不过，等待人工
        todo.append(row)
    if args.limit:
        todo = todo[: args.limit]
    total_pairs = sum(len(r.get("pairs") or []) for r in todo)
    logger.info(f"待复核 {len(todo)} 段 / {total_pairs} 句对（模型 {args.model}）")
    if args.dry_run:
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import Lock

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    lock = Lock()
    stats = {"agree": 0, "flagged": 0, "fail": 0}

    def job(row):
        return verify_row(client, args.model, row)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(job, r): r for r in todo}
        for future in as_completed(futures):
            row = futures[future]
            try:
                all_pass, failed = future.result()
            except Exception as exc:  # noqa: BLE001 —— 单段失败不中断
                with lock:
                    stats["fail"] += 1
                logger.warning(f"ch{row.get('chapter')}#{row.get('original_segment_index')} 复核失败: {str(exc)[:80]}")
                continue
            record = {
                "unit_key": unit_key_from_row(args.book, row),
                "book": args.book,
                "layer": row.get("layer"),
                "review_status": REVIEW_MODEL_AGREE if all_pass else REVIEW_CANDIDATE,
                "action": "model_verify",
                "review_note": (
                    f"自动复核通过（{len(row.get('pairs') or [])} 句）" if all_pass
                    else f"自动复核未过，第 {','.join(map(str, failed))} 句不对应"
                ),
                "reviewed_at": utc_now_iso(),
                "reviewer": f"model:{args.model}",
                "text_fingerprint": text_fingerprint(row.get("text") or ""),
                "original_segment_index": row.get("original_segment_index"),
                "translation_unit_index": row.get("translation_unit_index", 0),
                "source_paragraph_index": row.get("source_paragraph_index"),
                "segment_index": row.get("segment_index"),
                "volume": row.get("volume"),
                "chapter": row.get("chapter"),
                "marker": row.get("marker"),
                "translation_source": row.get("translation_source"),
                "failed_pairs": failed,
            }
            with lock:
                append_review(args.book, record)
                stats["agree" if all_pass else "flagged"] += 1
                done = stats["agree"] + stats["flagged"]
                if done % 200 == 0:
                    logger.info(f"进度 {done}/{len(todo)}（通过 {stats['agree']}，待人工 {stats['flagged']}）")
    logger.info(
        f"完成：模型一致 {stats['agree']} 段，标记待人工 {stats['flagged']} 段，失败 {stats['fail']}"
    )


if __name__ == "__main__":
    main()
