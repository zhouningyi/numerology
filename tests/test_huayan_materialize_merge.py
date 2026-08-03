"""华严物化合并：补跑不得冲掉已有 generated_layers。"""

import json
from pathlib import Path

import translate_huayan_segments as th


def test_materialize_merges_without_dropping_existing(tmp_path, monkeypatch):
    layer = tmp_path / "huayan_t0279_generated_layers.jsonl"
    existing = {
        "book": "huayan_t0279",
        "layer": "现代释译",
        "text": "旧译",
        "original_segment_index": 10,
        "translation_unit_index": 0,
        "segment_index": 10,
        "volume": 1,
        "chapter": 1,
        "confidence": "low",
        "review_status": "candidate",
    }
    layer.write_text(json.dumps(existing, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(th, "LAYER_PATH", layer)

    info = th.materialize([{
        "original_segment_index": 11,
        "unit_index": 0,
        "unit_count": 1,
        "source_text": "原文十一",
        "translation": "新译十一",
        "model": "test-model",
        "prompt_version": "v-test",
        "volume": 1,
        "chapter": 1,
        "max_chars": 700,
    }], merge=True)

    rows = [json.loads(line) for line in layer.read_text(encoding="utf-8").splitlines()]
    assert info["total"] == 2
    assert info["new_keys"] == 1
    texts = {row["original_segment_index"]: row["text"] for row in rows}
    assert texts[10] == "旧译"
    assert texts[11] == "新译十一"


def test_materialize_replace_overwrites(tmp_path, monkeypatch):
    layer = tmp_path / "huayan_t0279_generated_layers.jsonl"
    layer.write_text(json.dumps({
        "book": "huayan_t0279",
        "layer": "现代释译",
        "text": "旧译",
        "original_segment_index": 10,
        "translation_unit_index": 0,
        "segment_index": 10,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(th, "LAYER_PATH", layer)
    th.materialize([{
        "original_segment_index": 11,
        "unit_index": 0,
        "unit_count": 1,
        "source_text": "x",
        "translation": "only",
        "model": "m",
        "prompt_version": "v",
        "max_chars": 700,
    }], merge=False)
    rows = [json.loads(line) for line in layer.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["original_segment_index"] == 11
