"""译文人工复核写回。"""

from numerology.corpus_review import (
    apply_reviews_to_rows,
    build_review_record,
    text_fingerprint,
    unit_key_from_row,
)
from numerology.corpus_quality import REVIEW_HUMAN_VERIFIED, REVIEW_REJECTED


def test_unit_key_stable_for_generated_translation():
    row = {
        "layer": "现代释译",
        "original_segment_index": 12,
        "translation_unit_index": 0,
        "text": "白话",
    }
    assert unit_key_from_row("huayan_t0279", row) == "huayan_t0279|现代释译|o12|u0"


def test_apply_reviews_sets_human_verified_high():
    rows = [{
        "layer": "现代释译",
        "original_segment_index": 5,
        "translation_unit_index": 0,
        "text": "我这样听说：",
        "confidence": "low",
        "review_status": "candidate",
    }]
    key = unit_key_from_row("huayan_t0279", rows[0])
    reviews = {
        key: {
            "unit_key": key,
            "review_status": REVIEW_HUMAN_VERIFIED,
            "text_fingerprint": text_fingerprint(rows[0]["text"]),
            "review_note": "ok",
            "reviewed_at": "2026-08-04",
        }
    }
    fixed = apply_reviews_to_rows("huayan_t0279", rows, reviews)
    assert fixed[0]["review_status"] == REVIEW_HUMAN_VERIFIED
    assert fixed[0]["confidence"] == "high"
    assert fixed[0]["review_note"] == "ok"


def test_stale_review_when_text_changes():
    rows = [{
        "layer": "现代释译",
        "original_segment_index": 5,
        "translation_unit_index": 0,
        "text": "新译文",
        "confidence": "low",
    }]
    key = unit_key_from_row("huayan_t0279", rows[0])
    reviews = {
        key: {
            "unit_key": key,
            "review_status": REVIEW_HUMAN_VERIFIED,
            "text_fingerprint": text_fingerprint("旧译文"),
        }
    }
    fixed = apply_reviews_to_rows("huayan_t0279", rows, reviews)
    assert fixed[0]["review_status"] == "candidate"
    assert fixed[0].get("review_stale") is True


def test_build_review_record_actions():
    row = {
        "layer": "现代白话",
        "original_segment_index": 1,
        "translation_unit_index": 0,
        "text": "译",
        "volume": 1,
        "chapter": 1,
    }
    verified = build_review_record("huayan_t0279", row, "verify", note="好")
    assert verified["review_status"] == REVIEW_HUMAN_VERIFIED
    rejected = build_review_record("huayan_t0279", row, "reject")
    assert rejected["review_status"] == REVIEW_REJECTED
