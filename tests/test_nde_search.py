"""向量检索纯函数测试（不调用 API）。"""

import numpy as np

from numerology.nde.search import normalize, top_k


def test_normalize_unit_length():
    m = normalize(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert abs(np.linalg.norm(m[0]) - 1.0) < 1e-6
    assert np.all(m[1] == 0)  # 零向量不产生 NaN


def test_top_k_returns_most_similar_first():
    matrix = normalize(np.array([
        [1.0, 0.0],   # 与查询同向
        [0.0, 1.0],   # 正交
        [0.9, 0.1],   # 接近
    ]))
    results = top_k(matrix, np.array([1.0, 0.0]), k=2)
    assert [i for i, _ in results] == [0, 2]
    assert results[0][1] > results[1][1]


def test_top_k_handles_zero_query():
    matrix = normalize(np.array([[1.0, 0.0]]))
    assert top_k(matrix, np.array([0.0, 0.0])) == []


def test_index_source_filter(tmp_path):
    import json
    from numerology.nde.search import EmbeddingIndex

    matrix = normalize(np.array([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]]))
    np.save(tmp_path / "matrix.npy", matrix)
    meta = [
        {"source": "huayan", "title": "h1"},
        {"source": "nde", "title": "n1"},
        {"source": "nde", "title": "n2"},
    ]
    with (tmp_path / "meta.jsonl").open("w") as handle:
        for m in meta:
            handle.write(json.dumps(m) + "\n")
    index = EmbeddingIndex(tmp_path / "matrix.npy", tmp_path / "meta.jsonl")
    query = np.array([1.0, 0.0])
    all_results = index.search(query, k=3)
    assert all_results[0]["title"] == "h1"
    nde_only = index.search(query, k=3, sources={"nde"})
    assert [r["title"] for r in nde_only] == ["n1", "n2"]
    assert index.search(query, k=3, sources={"nonexistent"}) == []
