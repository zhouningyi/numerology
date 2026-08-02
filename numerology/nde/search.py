"""向量检索：加载嵌入索引，余弦相似度取 top-k。

索引由 build_embeddings.py 生成：matrix.npy（已归一化 float32）+ meta.jsonl。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

EMBED_DIR = Path("data/processed/embeddings")
MATRIX_PATH = EMBED_DIR / "matrix.npy"
META_PATH = EMBED_DIR / "meta.jsonl"


def normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


def top_k(matrix: np.ndarray, query: np.ndarray, k: int = 20) -> list[tuple[int, float]]:
    """已归一化矩阵与查询向量 → [(行号, 余弦相似度)] 按相似度降序。"""
    q = np.nan_to_num(query.astype(np.float32))
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return []
    q = q / q_norm
    scores = matrix @ q
    k = min(k, len(scores))
    idx = np.argpartition(-scores, k - 1)[:k]
    idx = idx[np.argsort(-scores[idx])]
    return [(int(i), float(scores[i])) for i in idx]


class EmbeddingIndex:
    """磁盘索引的轻量封装；文件更新后需重新实例化（服务端按 mtime 缓存）。"""

    def __init__(self, matrix_path: Path = MATRIX_PATH, meta_path: Path = META_PATH):
        self.matrix = np.load(matrix_path)
        with meta_path.open(encoding="utf-8") as handle:
            self.meta = [json.loads(line) for line in handle]

    def search(self, query_vector: np.ndarray, k: int = 20) -> list[dict]:
        results = []
        for row, score in top_k(self.matrix, query_vector, k):
            item = dict(self.meta[row])
            item["score"] = round(score, 4)
            results.append(item)
        return results
