import json

from align_canon_models import normalize_mapping, parse_json_object, repair_by_semantic_anchors


def test_alignment_parser_accepts_prefixed_indices():
    task = {
        "original_segments": [{"segment_index": 521}, {"segment_index": 522}],
        "modern_paragraphs": [{"paragraph_index": 0}, {"paragraph_index": 1}],
    }
    value = parse_json_object(json.dumps({
        "mapping": [
            {"modern_paragraph_index": "M0", "original_segment_indices": ["O521"], "relation": "translation"},
            {"modern_paragraph_index": "M1", "original_segment_indices": [522], "relation": "split"},
        ]
    }))
    mapping, errors = normalize_mapping(value, task)
    assert not errors
    assert mapping[0]["original_segment_indices"] == [521]
    assert mapping[1]["original_segment_indices"] == [522]


def test_alignment_parser_fills_missing_modern_paragraph():
    task = {
        "original_segments": [{"segment_index": 1}],
        "modern_paragraphs": [{"paragraph_index": 0}, {"paragraph_index": 1}],
    }
    mapping, errors = normalize_mapping(
        {"mapping": [{"modern_paragraph_index": 0, "original_segment_indices": [1]}]},
        task,
    )
    assert mapping[1]["original_segment_indices"] == []
    assert mapping[1]["confidence"] == "low"
    assert not errors


def test_semantic_anchor_keeps_split_translation_on_long_original():
    task = {
        "original_segments": [
            {"segment_index": 5, "text": "如是我聞。"},
            {"segment_index": 6, "text": "佛在摩竭提國阿蘭若法菩提場中。其地堅固金剛所成，上妙寶輪眾寶華清淨摩尼以為嚴飾。"},
            {"segment_index": 7, "text": "爾時世尊處于此座，於一切法成最正覺，智入三世悉皆平等。"},
        ],
        "modern_paragraphs": [
            {"paragraph_index": 0, "text": "这是我亲自听闻的。"},
            {"paragraph_index": 1, "text": "当时佛陀在摩竭提国的菩提道场成佛。"},
            {"paragraph_index": 2, "text": "道场以金刚为地基，宝轮、宝花和摩尼装饰得十分庄严。"},
            {"paragraph_index": 3, "text": "这时世尊安坐此座，智慧进入三世而平等。"},
        ],
    }
    mapping = {
        0: {"original_segment_indices": [5]},
        1: {"original_segment_indices": [6]},
        2: {"original_segment_indices": [7]},  # 模拟错误的一对一推进
        3: {"original_segment_indices": [7]},
    }
    repaired, repairs = repair_by_semantic_anchors(task, mapping)
    assert repaired[2]["original_segment_indices"] == [6]
    assert repaired[3]["original_segment_indices"] == [7]
    assert repairs
