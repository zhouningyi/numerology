"""译文/对齐的人工复核写回。

复核结果单独落在 data/processed/canon/reviews/<book>_reviews.jsonl，
不直接改生成层，避免重跑 pipeline 冲掉人工结论。加载语料时按 unit_key 覆盖。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from numerology.corpus_quality import (
    REVIEW_CANDIDATE,
    REVIEW_HUMAN_VERIFIED,
    REVIEW_REJECTED,
    STATUS_LABELS,
    confidence_for_review,
    utc_now_iso,
)

REVIEWS_DIR = Path("data/processed/canon/reviews")
ALLOWED_ACTIONS = {"verify", "reject", "reset"}
ALLOWED_LAYERS = {"现代白话", "现代释译"}


def text_fingerprint(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text or "")
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:16]


def unit_key(
    book: str,
    layer: str,
    *,
    original_segment_index: int | None = None,
    translation_unit_index: int | None = 0,
    source_paragraph_index: int | None = None,
    segment_index: int | None = None,
    volume: int | None = None,
    chapter: int | None = None,
) -> str:
    """稳定复核键：优先原文段号 + 单元；对齐白话用 source_paragraph + volume/chapter。"""
    if original_segment_index is not None:
        unit = 0 if translation_unit_index is None else translation_unit_index
        return f"{book}|{layer}|o{original_segment_index}|u{unit}"
    if source_paragraph_index is not None and volume is not None and chapter is not None:
        return f"{book}|{layer}|v{volume}|c{chapter}|p{source_paragraph_index}"
    if segment_index is not None:
        return f"{book}|{layer}|s{segment_index}"
    raise ValueError("无法构造 unit_key：缺少段号字段")


def unit_key_from_row(book: str, row: dict) -> str | None:
    try:
        targets = row.get("original_segment_indices") or []
        original = row.get("original_segment_index")
        if original is None and targets:
            original = targets[0]
        return unit_key(
            book,
            row.get("layer") or "现代释译",
            original_segment_index=original if original is not None else None,
            translation_unit_index=row.get("translation_unit_index", 0),
            source_paragraph_index=row.get("source_paragraph_index"),
            segment_index=row.get("segment_index"),
            volume=row.get("volume"),
            chapter=row.get("chapter"),
        )
    except (TypeError, ValueError):
        return None


def reviews_path(book: str, base: Path | None = None) -> Path:
    root = base or REVIEWS_DIR
    return root / f"{book}_reviews.jsonl"


def load_reviews(book: str, base: Path | None = None) -> dict[str, dict]:
    path = reviews_path(book, base)
    if not path.exists():
        return {}
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = row.get("unit_key")
        if key:
            latest[key] = row
    return latest


def append_review(book: str, record: dict, base: Path | None = None) -> Path:
    path = reviews_path(book, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def apply_reviews_to_rows(
    book: str,
    rows: list[dict],
    reviews: dict[str, dict] | None = None,
) -> list[dict]:
    """把人工复核覆盖到译文层；非译文层原样返回。"""
    reviews = reviews if reviews is not None else load_reviews(book)
    if not reviews:
        return rows
    output = []
    for row in rows:
        item = dict(row)
        if item.get("layer") not in ALLOWED_LAYERS:
            output.append(item)
            continue
        key = unit_key_from_row(book, item)
        if not key or key not in reviews:
            output.append(item)
            continue
        review = reviews[key]
        status = review.get("review_status", REVIEW_CANDIDATE)
        # 文本指纹变化则降为 candidate，避免复核挂到被改写的译文
        fp = review.get("text_fingerprint")
        if fp and fp != text_fingerprint(item.get("text") or ""):
            item["review_status"] = REVIEW_CANDIDATE
            item["confidence"] = confidence_for_review(REVIEW_CANDIDATE)
            item["alignment_status"] = "复核过期：译文已变更，需重新确认"
            item["review_stale"] = True
            output.append(item)
            continue
        item["review_status"] = status
        item["confidence"] = confidence_for_review(status)
        item["alignment_status"] = STATUS_LABELS.get(status, status)
        item["review_note"] = review.get("review_note") or ""
        item["reviewed_at"] = review.get("reviewed_at")
        item["reviewer"] = review.get("reviewer") or "local"
        item["unit_key"] = key
        output.append(item)
    return output


def build_review_record(
    book: str,
    row: dict,
    action: str,
    *,
    note: str = "",
    reviewer: str = "local",
) -> dict[str, Any]:
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"未知 action: {action}")
    if row.get("layer") not in ALLOWED_LAYERS:
        raise ValueError("只能复核现代白话/现代释译")
    key = unit_key_from_row(book, row)
    if not key:
        raise ValueError("无法定位译文单元")
    if action == "verify":
        status = REVIEW_HUMAN_VERIFIED
    elif action == "reject":
        status = REVIEW_REJECTED
    else:
        status = REVIEW_CANDIDATE
    return {
        "unit_key": key,
        "book": book,
        "layer": row.get("layer"),
        "review_status": status,
        "action": action,
        "review_note": note.strip(),
        "reviewed_at": utc_now_iso(),
        "reviewer": reviewer,
        "text_fingerprint": text_fingerprint(row.get("text") or ""),
        "original_segment_index": row.get("original_segment_index")
        if row.get("original_segment_index") is not None
        else (row.get("original_segment_indices") or [None])[0],
        "translation_unit_index": row.get("translation_unit_index", 0),
        "source_paragraph_index": row.get("source_paragraph_index"),
        "segment_index": row.get("segment_index"),
        "volume": row.get("volume"),
        "chapter": row.get("chapter"),
        "marker": row.get("marker"),
        "translation_source": row.get("translation_source"),
    }


def find_row_by_unit_key(book: str, rows: list[dict], key: str) -> dict | None:
    for row in rows:
        if row.get("layer") not in ALLOWED_LAYERS:
            continue
        if unit_key_from_row(book, row) == key:
            return row
    return None
