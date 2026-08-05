"""通用主题发现：聚类 + 自动起名。"""

import numpy as np

from numerology.topic_discovery import (
    discover_topics,
    label_cluster_extractive,
    tokenize,
)


def test_tokenize_basic():
    assert "light" in tokenize("I saw a brilliant white light")


def test_extractive_title_from_keywords():
    docs = [
        "I saw a tunnel and a bright light at the end",
        "There was a dark tunnel then brilliant light",
        "Moving through a tunnel toward the light",
    ]
    title, kws = label_cluster_extractive(docs)
    assert title
    assert kws
    assert any(w in {"tunnel", "light", "bright", "brilliant"} for w in kws)


def test_discover_topics_separates_simple_groups():
    # 两组正交向量 + 对应文档
    rng = np.random.default_rng(0)
    a = np.tile(np.array([[1.0, 0.0, 0.0]], dtype=np.float32), (12, 1))
    b = np.tile(np.array([[0.0, 1.0, 0.0]], dtype=np.float32), (12, 1))
    a += rng.normal(0, 0.02, a.shape).astype(np.float32)
    b += rng.normal(0, 0.02, b.shape).astype(np.float32)
    matrix = np.vstack([a, b])
    docs = (
        ["tunnel light bright white light experience"] * 12
        + ["music choir singing beautiful melody hymn"] * 12
    )
    result = discover_topics(matrix, docs, n_clusters=2, algorithm="kmeans")
    assert result.n_clusters == 2
    titles = " ".join(c.title.lower() for c in result.clusters)
    # 至少一侧关键词应进入标题
    assert ("tunnel" in titles or "light" in titles or "music" in titles or "choir" in titles)
    assert all(c.size >= 8 for c in result.clusters)
