#!/usr/bin/env python3
"""古籍逐段释译（v3）：以互联网译文为参考底本，逐句对齐输出。

改进（相对 v2 的整段自由翻译）：
1. **参考底本**：每段附上互联网译文（华严经为洪启嵩译）在本章中按位置比例
   截取的窗口，要求模型优先摘取/贴合参考译文，未覆盖处仿其文风补译；
2. **句级对照**：原文按句切分编号，模型逐句返回译文——"圈到哪一句，
   就有哪一句的翻译"由构造保证（pairs 字段）。

输出 <book>_generated_layers.jsonl，含 pairs=[[原句,译句],...] 与合并 text。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import defaultdict
from pathlib import Path

from scripts.nde.translate_nderf import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LAYERS_DIR = Path("data/processed/canon/layers")
PROMPT_VERSION = "seg-sent-ref-v3"

SYSTEM_PROMPT = """把佛经原文逐句翻译成现代汉语。给你两份材料：
1. 编号的原文句子列表；
2. 一段"参考译文"（出版译本的节选，覆盖本段附近内容，可能不完全对应）。

规则：
- 逐句翻译：每个编号输出一句译文，与原句一一对应；
- **优先使用参考译文**：参考译文中能对应上的句子，摘取或贴合其措辞；
  参考未覆盖的句子，模仿参考译文的文风自行翻译；
- 偈颂（诗体）同样逐句完整翻译，不概括不省略；
- 专名保留原名；译文完整通顺，不加评论。
只输出 JSON：{"1": "第1句译文", "2": "第2句译文", ...}"""

# 句切分：按句末标点切，保留标点；引号内的句号不强拆（古文引号「」较规整）
_SENT_RE = re.compile(r"[^。！？]*[。！？]」?|[^。！？]+$")


def split_sentences(text: str, max_len: int = 160) -> list[str]:
    raw = [s.strip() for s in _SENT_RE.findall(text) if s.strip()]
    out: list[str] = []
    for sentence in raw:
        while len(sentence) > max_len:  # 超长句按分号再切
            cut = sentence.rfind("；", 0, max_len)
            if cut <= 0:
                cut = max_len
            out.append(sentence[: cut + 1])
            sentence = sentence[cut + 1 :]
        if sentence:
            out.append(sentence)
    return out


def load_references(book: str) -> dict[str, str]:
    """章号 → 互联网译文全文（多块拼接，按 segment_index 排序）。"""
    path = LAYERS_DIR / f"{book}_modern_layers.jsonl"
    if not path.exists():
        return {}
    blocks = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            # 卷号是稳定的先后依据；补爬块的 segment_index 编号体系与旧块不同
            try:
                volume = int(str(row.get("volume") or 0))
            except ValueError:
                volume = 0
            blocks[str(row.get("chapter"))].append(
                (volume, int(str(row.get("segment_index") or 0)), row.get("text", ""))
            )
    return {
        chapter: "\n".join(text for _, _, text in sorted(items))
        for chapter, items in blocks.items()
    }


def reference_window(full: str, position_ratio: float, width: int = 5000) -> str:
    """按段落在章内的位置比例截取参考窗口（译文顺序与原文大体一致）。"""
    if not full:
        return ""
    if len(full) <= width:
        return full
    center = int(len(full) * position_ratio)
    start = max(0, center - width // 2)
    return full[start : start + width]


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
                if row.get("prompt_version") != PROMPT_VERSION:
                    continue  # 旧版本行不算完成，v3 全部重做
                done.add((str(row.get("chapter")), str(row.get("original_segment_index"))))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def translate_one(client, model: str, effort: str, segment: dict, reference: str) -> dict:
    sentences = split_sentences(segment["text"])
    if not sentences:
        return {}
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    user = f"原文句子：\n{numbered}\n\n参考译文：\n{reference or '（本章无参考译文，请自行翻译）'}"
    kwargs = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user[:48000]},
        ],
    }
    if model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = effort
    else:
        kwargs["temperature"] = 0.2
    response = client.chat.completions.create(**kwargs)
    raw = json.loads(response.choices[0].message.content)
    pairs = []
    for i, sentence in enumerate(sentences):
        trans = raw.get(str(i + 1))
        pairs.append([sentence, trans.strip() if isinstance(trans, str) else ""])
    return {"pairs": pairs, "text": "\n".join(p[1] for p in pairs if p[1])}


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
    references = load_references(args.book)
    # 章内位置比例：用于截取参考窗口
    by_chapter = defaultdict(list)
    for r in originals:
        by_chapter[str(r.get("chapter"))].append(r)
    position = {}
    for chapter, items in by_chapter.items():
        for i, r in enumerate(items):
            position[id(r)] = i / max(1, len(items) - 1) if len(items) > 1 else 0.5

    done = load_done(args.book)
    todo = [
        r for r in originals
        if (str(r.get("chapter")), str(r.get("segment_index"))) not in done
    ]
    if args.limit:
        todo = todo[: args.limit]
    covered = sum(1 for r in todo if references.get(str(r.get("chapter"))))
    logger.info(
        f"{args.book}: 原文段 {len(originals)}，v3 已译 {len(done)}，本次 {len(todo)} 段"
        f"（有参考译文 {covered} 段，模型 {args.model}/{args.reasoning_effort}）"
    )
    if args.dry_run:
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import Lock

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    count = fail = 0
    lock = Lock()

    def job(segment):
        chapter_ref = references.get(str(segment.get("chapter")), "")
        window = reference_window(chapter_ref, position.get(id(segment), 0.5))
        return translate_one(client, args.model, args.reasoning_effort, segment, window)

    with out_path(args.book).open("a", encoding="utf-8") as out, ThreadPoolExecutor(
        max_workers=args.workers
    ) as pool:
        futures = {pool.submit(job, r): r for r in todo}
        for future in as_completed(futures):
            segment = futures[future]
            try:
                result = future.result()
                if not result.get("text"):
                    raise ValueError("空译文")
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
                    "translation_source": (
                        "洪启嵩译参考 + " + args.model
                        if references.get(str(segment.get("chapter")))
                        else f"项目生成（{args.model}，本章无参考）"
                    ),
                    "alignment_method": "逐句对齐生成，参考互联网译文",
                    "alignment_status": "已对齐",
                    "prompt_version": PROMPT_VERSION,
                    "text": result["text"],
                    "pairs": result["pairs"],
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
                logger.warning(
                    f"ch{segment.get('chapter')}#{segment.get('segment_index')} 失败: {str(exc)[:100]}"
                )
    logger.info(f"完成：新译 {count} 段，失败 {fail} -> {out_path(args.book)}")


if __name__ == "__main__":
    main()
