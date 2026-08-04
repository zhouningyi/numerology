#!/usr/bin/env python3
"""华严经译文对齐（v4）：互联网译文逐字上屏，模型只做句子对齐。

与 v3（AI 参考改写）的本质区别：**译文不经过模型生成**。
洪启嵩译按章拆句编号，模型对每个原文句只返回对应的译文句编号，
译文由编号逐字提取——屏幕上的每个字都来自出版译本，由构造保证。
译本未覆盖的句子才用模型补译，并逐句标记（pair_sources: ref/ai）。

章内顺序单调：译文与原文语序一致，游标只前进，窗口小而准。
输出并入 <book>_generated_layers.jsonl（prompt_version=ref-align-v4）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import defaultdict
from pathlib import Path

from translate_nderf import load_dotenv
from translate_canon_segments import load_references, split_sentences

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LAYERS_DIR = Path("data/processed/canon/layers")
PROMPT_VERSION = "ref-align-v5"
WINDOW_SENTS = 90     # 每次给模型看的译文句窗口
CURSOR_BACKOFF = 3    # 游标回退量，容忍轻微乱序

# 参考译文里偶发的网页装饰；选句前剔除
_REF_JUNK_RE = re.compile(
    r"(\[详情\]|放大字体|缩小|关闭|【原典】|作者：洪启嵩|\[投稿\]|"
    r"白话华严经\s*第.*?卷|华严经是大乘佛教修学.*)",
)

ALIGN_PROMPT = """给你两组编号句子：佛经原文句（古文）和出版译本的译文句（现代文）。
译文与原文顺序一致。任务：为每个原文句找出对应的译文句编号。

规则：
- 一个原文句可对应 1 个或多个**连续**的译文句编号；
- 译文窗口中确实没有对应内容时返回 null，不要硬配；
- 只输出 JSON：{"原文句号": [译文句号,...] 或 null, ...}"""

FILL_PROMPT = """把下列佛经原文句子翻译成现代汉语，风格与给出的译文样例一致。
逐句翻译，完整通顺，专名保留。只输出 JSON：{"句号": "译文", ...}"""


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
                if row.get("prompt_version") == PROMPT_VERSION:
                    done.add((str(row.get("chapter")), str(row.get("original_segment_index"))))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def _chat_json(client, model: str, effort: str, system: str, user: str) -> dict:
    kwargs = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user[:48000]},
        ],
    }
    if model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = effort
    response = client.chat.completions.create(**kwargs)
    return json.loads(response.choices[0].message.content)


from zhconv import convert as _zh_convert


def _bigrams(text: str) -> set:
    # 原文为繁体、洪译为简体：先归一化到简体再取二元组，否则重叠被大幅低估
    stripped = _zh_convert(re.sub(r"[，。！？；、：\s]", "", text), "zh-cn")
    return {stripped[i : i + 2] for i in range(len(stripped) - 1)}


def find_anchor(seg_text: str, ref_sents: list[str], ref_bigrams: list[set],
                start: int, est_sents: int) -> int:
    """在 ref_sents[start:] 中用二元组重叠找本段最可能的起始句号。

    专名与术语在古文→白话间大量保留，是可靠的词汇锚点；
    单调约束（只向后搜）避免华严套语（"佛子"式重复）误配到前文。
    """
    seg_grams = _bigrams(seg_text)
    if not seg_grams:
        return start
    width = max(6, est_sents)
    stride = max(1, width // 2)
    # 前跳限界：套语/偈颂误配到远处会连累单调游标，宁可就近降级补译
    search_end = min(len(ref_sents), start + est_sents * 8 + 300)
    best_score, best_pos = -1, start
    pos = start
    while pos < search_end:
        window_grams = set().union(*ref_bigrams[pos : pos + width]) if ref_bigrams[pos : pos + width] else set()
        score = len(seg_grams & window_grams)
        if score > best_score:
            best_score, best_pos = score, pos
        pos += stride
    return best_pos


def _is_junk_ref_sentence(text: str) -> bool:
    from numerology.translation_display import contains_web_junk
    return contains_web_junk(text or "") or bool(_REF_JUNK_RE.search(text or ""))


def clean_ref_sentences(ref_sents: list[str]) -> list[str]:
    return [s for s in ref_sents if s and not _is_junk_ref_sentence(s)]


def align_segment(client, model, effort, orig_sents, ref_sents, cursor,
                  window_sents: int = WINDOW_SENTS):
    """返回 (pairs, sources, new_cursor)。译文句逐字取自 ref_sents。"""
    window_start = max(0, cursor - CURSOR_BACKOFF)
    window = ref_sents[window_start : window_start + window_sents]
    orig_block = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(orig_sents))
    ref_block = "\n".join(
        f"{window_start + j + 1}. {s}" for j, s in enumerate(window)
    )
    mapping = _chat_json(
        client, model, effort, ALIGN_PROMPT,
        f"原文句：\n{orig_block}\n\n译文句（第 {window_start + 1}-{window_start + len(window)} 句）：\n{ref_block}",
    )
    pairs, sources = [], []
    max_used = cursor
    unmatched: dict[str, str] = {}
    for i, orig in enumerate(orig_sents):
        indexes = mapping.get(str(i + 1))
        picked = []
        if isinstance(indexes, list):
            picked = [
                ref_sents[j - 1]
                for j in indexes
                if isinstance(j, int)
                and window_start + 1 <= j <= window_start + len(window)
                and not _is_junk_ref_sentence(ref_sents[j - 1])
            ]
            if indexes and isinstance(indexes[-1], int):
                max_used = max(max_used, indexes[-1])
        # 本地校验：命中句与原句需有词汇重叠（繁简归一后），
        # 防止模型在窗口内硬配不相关内容
        if picked:
            joined = "".join(picked)
            if _is_junk_ref_sentence(joined):
                picked = []
            else:
                overlap = len(_bigrams(orig) & _bigrams(joined))
                if overlap < (1 if len(orig) <= 12 else 2):
                    picked = []
                # 伪繁简：几乎只是原文转简体，不算译本命中
                from numerology.translation_display import is_simplified_only
                if picked and is_simplified_only(orig, joined):
                    picked = []
        if picked:
            pairs.append([orig, "".join(picked)])
            sources.append("ref")
        else:
            pairs.append([orig, ""])
            sources.append("ai")
            unmatched[str(i + 1)] = orig
    # 译本未覆盖的句子：模型按译本文风补译，并保持标记
    if unmatched:
        sample = "".join(ref_sents[window_start : window_start + 5])
        fill = _chat_json(
            client, model, effort, FILL_PROMPT,
            f"译文样例：{sample}\n\n待译原文句：\n" + json.dumps(unmatched, ensure_ascii=False),
        )
        for key, orig in unmatched.items():
            trans = fill.get(key)
            if isinstance(trans, str) and trans.strip():
                pairs[int(key) - 1][1] = trans.strip()
    return pairs, sources, max(cursor, max_used - CURSOR_BACKOFF)


def process_chapter(client, model, effort, chapter, segments, ref_text, done, out, lock, stats):
    ref_sents = clean_ref_sentences(split_sentences(ref_text))
    ref_bigrams = [_bigrams(s) for s in ref_sents]
    avg_ref_len = max(10, sum(len(s) for s in ref_sents) // max(1, len(ref_sents)))
    cursor = 0
    for segment in segments:
        key = (str(chapter), str(segment.get("segment_index")))
        if key in done:
            continue
        orig_sents = split_sentences(segment["text"])
        if not orig_sents:
            continue
        # 词汇锚点定位：从上次位置向后搜二元组重叠峰值，长章/套语下仍稳
        est_sents = int(len(segment["text"]) * 2.4 / avg_ref_len) + 2
        anchor = find_anchor(
            segment["text"], ref_sents, ref_bigrams,
            max(0, cursor - CURSOR_BACKOFF), est_sents,
        )
        try:
            pairs, sources, cursor = align_segment(
                client, model, effort, orig_sents, ref_sents, anchor,
                window_sents=max(WINDOW_SENTS, est_sents + 30),
            )
        except Exception as exc:  # noqa: BLE001 —— 单段失败跳过，下段继续
            with lock:
                stats["fail"] += 1
            logger.warning(f"ch{chapter}#{segment.get('segment_index')} 失败: {str(exc)[:100]}")
            continue
        ref_ratio = sources.count("ref") / max(1, len(sources))
        text = "\n".join(p[1] for p in pairs if p[1])
        if not text.strip():
            continue
        record = {
            "book": segment.get("book"),
            "chapter": segment.get("chapter"),
            "chapter_title": segment.get("chapter_title"),
            "book_chapter_label": segment.get("book_chapter_label"),
            "volume": segment.get("volume"),
            "source_file": segment.get("source_file"),
            "layer": "现代释译",
            # 模型对齐不得直接 high；须人工 verified
            "confidence": "low",
            "review_status": "candidate",
            "marker": None,
            "translation_source": "洪启嵩译（模型仅对齐）",
            "alignment_method": f"句级对齐提取，逐字取自译本；补译占比 {1 - ref_ratio:.0%}",
            "alignment_status": "候选（待复核）",
            "prompt_version": PROMPT_VERSION,
            "text": text,
            "pairs": pairs,
            "pair_sources": sources,
            "segment_index": segment.get("segment_index"),
            "original_segment_index": segment.get("segment_index"),
            "original_segment_indices": [segment.get("segment_index")],
        }
        with lock:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            stats["count"] += 1
            stats["ref_sents"] += sources.count("ref")
            stats["ai_sents"] += sources.count("ai")
            if stats["count"] % 100 == 0:
                logger.info(f"进度 {stats['count']} 段（补译句占比 "
                            f"{stats['ai_sents'] / max(1, stats['ai_sents'] + stats['ref_sents']):.0%}）")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default="huayan_t0279")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--reasoning-effort", default="minimal",
                        help="同语言选号任务，minimal 档足够")
    parser.add_argument("--chapter-workers", type=int, default=8,
                        help="章间并行；章内因游标必须顺序")
    parser.add_argument("--min-chars", type=int, default=30)
    parser.add_argument("--chapters", nargs="*", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    with (LAYERS_DIR / f"{args.book}_layers.jsonl").open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    references = load_references(args.book)
    by_chapter = defaultdict(list)
    for r in rows:
        text = r.get("text", "")
        # 短开经句（如「如是我聞：」）也必须对齐，不能因 min_chars 丢掉
        if r.get("layer") == "原文" and (
            len(text) >= args.min_chars or len(text.strip()) >= 2
        ):
            by_chapter[str(r.get("chapter"))].append(r)
    chapters = [
        c for c in by_chapter
        if c in references and (not args.chapters or c in args.chapters)
    ]
    done = load_done(args.book)
    todo_segments = sum(
        1 for c in chapters for s in by_chapter[c]
        if (c, str(s.get("segment_index"))) not in done
    )
    logger.info(f"有参考章 {len(chapters)} 个，待对齐 {todo_segments} 段"
                f"（模型 {args.model}/{args.reasoning_effort}）")
    if args.dry_run:
        return

    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    stats = {"count": 0, "fail": 0, "ref_sents": 0, "ai_sents": 0}
    lock = Lock()
    with out_path(args.book).open("a", encoding="utf-8") as out, ThreadPoolExecutor(
        max_workers=args.chapter_workers
    ) as pool:
        futures = [
            pool.submit(
                process_chapter, client, args.model, args.reasoning_effort,
                chapter, by_chapter[chapter], references[chapter],
                done, out, lock, stats,
            )
            for chapter in chapters
        ]
        for future in futures:
            future.result()
    total_sents = max(1, stats["ref_sents"] + stats["ai_sents"])
    logger.info(
        f"完成：对齐 {stats['count']} 段，失败 {stats['fail']}；"
        f"译本句 {stats['ref_sents']}（{stats['ref_sents']/total_sents:.0%}），"
        f"补译句 {stats['ai_sents']}（{stats['ai_sents']/total_sents:.0%}）"
    )


if __name__ == "__main__":
    main()
