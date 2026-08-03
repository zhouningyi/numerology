#!/usr/bin/env python3
"""古籍逐段现代释译生成：一段原文对应一段释译（1:1 对齐由构造保证）。

释译行写入 data/processed/canon/layers/<book>_generated_layers.jsonl，
携带 original_segment_index 直挂原文段下（server 的 inline 通道已支持）。
偈颂诗体逐句全文翻译，不概括、不省略。

用法:
    python3 translate_canon_segments.py --book huayan_t0279 --dry-run
    python3 translate_canon_segments.py --book huayan_t0279 --limit 5
    python3 translate_canon_segments.py --book huayan_t0279
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from translate_nderf import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LAYERS_DIR = Path("data/processed/canon/layers")

SYSTEM_PROMPT = """把下面这段佛经原文完整翻译成现代汉语。
要求：
- 忠实、完整、通顺，逐句对应，不概括、不省略、不添加评论或科判；
- 偈颂（诗体）也要逐句全文翻译，可以不押韵但必须完整；
- 专名（佛菩萨名、地名、法数名相）保留原名，首次出现可在括号内简注；
- 只输出译文本身。"""


def out_path(book: str) -> Path:
    return LAYERS_DIR / f"{book}_generated_layers.jsonl"


def load_done(book: str) -> set[tuple]:
    done = set()
    path = out_path(book)
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
                if row.get("original_segment_index") is not None:
                    done.add((str(row.get("chapter")), str(row.get("original_segment_index"))))
                # 兼容早期本地模型行的 original_segment_indices 列表格式
                for index in row.get("original_segment_indices") or []:
                    done.add((str(row.get("chapter")), str(index)))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def translate_one(client, model: str, effort: str, segment: dict) -> str:
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": segment["text"][:16000]},
        ],
    }
    if model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = effort
    else:
        kwargs["temperature"] = 0.2
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default="huayan_t0279")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--reasoning-effort", default="low",
                        help="骈文/偈颂在 minimal 档会退化为转写，low 档起译文才可靠")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--min-chars", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    source = LAYERS_DIR / f"{args.book}_layers.jsonl"
    with source.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    originals = [
        r for r in rows
        if r.get("layer") == "原文" and len(r.get("text", "")) >= args.min_chars
    ]
    done = load_done(args.book)
    todo = [
        r for r in originals
        if (str(r.get("chapter")), str(r.get("segment_index"))) not in done
    ]
    if args.limit:
        todo = todo[: args.limit]
    chars = sum(len(r["text"]) for r in todo)
    logger.info(
        f"{args.book}: 原文段 {len(originals)}，已译 {len(done)}，本次 {len(todo)} 段"
        f"（约 {chars/10000:.0f} 万字，模型 {args.model}/{args.reasoning_effort}）"
    )
    if args.dry_run:
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import Lock

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    count = fail = 0
    lock = Lock()
    with out_path(args.book).open("a", encoding="utf-8") as out, ThreadPoolExecutor(
        max_workers=args.workers
    ) as pool:
        futures = {
            pool.submit(translate_one, client, args.model, args.reasoning_effort, r): r
            for r in todo
        }
        for future in as_completed(futures):
            segment = futures[future]
            try:
                text = future.result()
                record = {
                    "book": args.book,
                    "chapter": segment.get("chapter"),
                    "chapter_title": segment.get("chapter_title"),
                    "book_chapter_label": segment.get("book_chapter_label"),
                    "volume": segment.get("volume"),
                    "source_file": segment.get("source_file"),
                    "layer": "现代释译",
                    "confidence": "high",
                    "marker": None,
                    "translation_source": f"项目生成（{args.model}）",
                    "alignment_method": "逐段生成，1:1 对齐",
                    "alignment_status": "已对齐",
                    "text": text,
                    "segment_index": f"gen-{segment.get('segment_index')}",
                    "original_segment_index": segment.get("segment_index"),
                }
                with lock:
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    count += 1
                    if count % 200 == 0:
                        out.flush()
                        logger.info(f"进度 {count}/{len(todo)}（失败 {fail}）")
            except Exception as exc:  # noqa: BLE001 —— 单段失败不中断批量
                with lock:
                    fail += 1
                logger.warning(f"ch{segment.get('chapter')}#{segment.get('segment_index')} 失败: {str(exc)[:100]}")
    logger.info(f"完成：新译 {count} 段，失败 {fail} -> {out_path(args.book)}")


if __name__ == "__main__":
    main()
