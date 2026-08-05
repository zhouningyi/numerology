"""通用主题自动发现：嵌入聚类 + 自动起名。

标准流水线（无特定领域无关）：

1. 文档 → 向量（embedding）
2. 聚类（KMeans / 层次聚类 / HDBSCAN…）
3. 对每个 cluster：
   - 抽代表文档
   - **自动起名**：TF-IDF 关键词 或 LLM 用 3–8 字总结
4. （可选）层次合并 / 映射到已有 ontology

本模块不绑定濒死；NDE 只是调用方之一。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Sequence

import numpy as np


_TOKEN_RE = re.compile(r"[a-zA-Z\u4e00-\u9fff][a-zA-Z\u4e00-\u9fff'-]{1,}")


@dataclass
class ClusterLabel:
    cluster_id: int
    size: int
    # 自动起名结果
    title: str                      # 几个字的总结（优先 LLM，否则关键词拼）
    keywords: list[str] = field(default_factory=list)
    # 代表样本
    exemplars: list[dict] = field(default_factory=list)
    # 诊断
    cohesion: float = 0.0           # 簇内平均余弦相似度（越高越紧）
    method: str = "tfidf"


@dataclass
class TopicDiscoveryResult:
    n_docs: int
    n_clusters: int
    labels: list[int]
    clusters: list[ClusterLabel]
    algorithm: str
    note: str = ""


def tokenize(text: str, *, extra_stop: Iterable[str] | None = None) -> list[str]:
    text = (text or "").lower()
    tokens = _TOKEN_RE.findall(text)
    stop_en = {
        "the", "and", "that", "was", "were", "with", "from", "this", "have",
        "had", "for", "not", "but", "are", "his", "her", "they", "them",
        "you", "your", "into", "then", "when", "what", "there", "their",
        "about", "would", "could", "been", "being", "very", "just", "like",
        "all", "out", "can", "said", "i", "me", "my", "we", "it", "is",
        "a", "an", "of", "to", "in", "on", "at", "as", "or", "by", "he",
        "she", "did", "do", "so", "if", "no", "yes", "up", "down", "over",
        "don't", "didn't", "him", "himself", "myself", "after", "before",
        "be", "am", "im", "it's", "that's",
    }
    if extra_stop:
        stop_en |= {s.lower() for s in extra_stop}
    return [t for t in tokens if t not in stop_en and len(t) > 1]


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


def cluster_embeddings(
    matrix: np.ndarray,
    *,
    n_clusters: int | None = None,
    algorithm: str = "kmeans",
    random_state: int = 42,
) -> tuple[np.ndarray, str]:
    """对已归一化/未归一化向量聚类，返回 labels 与算法名。"""
    from sklearn.cluster import AgglomerativeClustering, KMeans, MiniBatchKMeans

    x = np.asarray(matrix, dtype=np.float32)
    if x.ndim != 2 or len(x) == 0:
        raise ValueError("matrix 需要是非空二维数组")
    n = len(x)
    if n_clusters is None:
        # 经验：sqrt(n/2) 夹在 [8, 40]
        n_clusters = max(8, min(40, int(round(math.sqrt(n / 2)))))
    n_clusters = max(2, min(n_clusters, n))

    if algorithm == "agglomerative" and n <= 4000:
        model = AgglomerativeClustering(n_clusters=n_clusters, metric="cosine", linkage="average")
        labels = model.fit_predict(x)
        return labels.astype(int), f"agglomerative_k{n_clusters}"
    if algorithm == "minibatch" or n > 8000:
        model = MiniBatchKMeans(
            n_clusters=n_clusters, random_state=random_state, batch_size=1024, n_init=3,
        )
        labels = model.fit_predict(l2_normalize(x))
        return labels.astype(int), f"minibatch_kmeans_k{n_clusters}"
    model = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=10)
    labels = model.fit_predict(l2_normalize(x))
    return labels.astype(int), f"kmeans_k{n_clusters}"


def _tfidf_keywords(
    docs: Sequence[str],
    *,
    top_k: int = 6,
    max_df_ratio: float = 0.6,
    extra_stop: Iterable[str] | None = None,
) -> list[str]:
    """簇内 TF-IDF 关键词（无需外部模型）。"""
    tokenized = [tokenize(d, extra_stop=extra_stop) for d in docs]
    df = Counter()
    tfs = []
    for tokens in tokenized:
        tf = Counter(tokens)
        tfs.append(tf)
        for t in tf:
            df[t] += 1
    n = max(1, len(docs))
    scores: Counter[str] = Counter()
    for tf in tfs:
        for term, cnt in tf.items():
            if n > 3 and df[term] / n > max_df_ratio:
                continue
            idf = math.log((n + 1) / (df[term] + 1)) + 1.0
            scores[term] += (cnt / max(1, sum(tf.values()))) * idf
    # 簇内高度同质时 IDF 过滤会清空，退回词频
    if not scores:
        for tf in tfs:
            scores.update(tf)
    return [w for w, _ in scores.most_common(top_k)]


def _cohesion(matrix: np.ndarray, indices: list[int]) -> float:
    if len(indices) < 2:
        return 1.0
    sub = l2_normalize(matrix[indices])
    # 平均成对余弦 ≈ (||sum||^2 - n) / (n(n-1))
    s = sub.sum(axis=0)
    n = len(indices)
    total = float(s @ s) - n
    denom = n * (n - 1)
    return max(0.0, min(1.0, total / denom)) if denom else 0.0


def label_cluster_extractive(
    docs: Sequence[str],
    *,
    top_k: int = 5,
    extra_stop: Iterable[str] | None = None,
) -> tuple[str, list[str]]:
    """无 LLM：用关键词拼 2–5 个词的标题。"""
    kws = _tfidf_keywords(docs, top_k=top_k, extra_stop=extra_stop)
    if not kws:
        return "未命名主题", []
    # 几个字/词的总结
    title = " · ".join(kws[:3])
    return title, kws


def label_cluster_with_llm(
    docs: Sequence[str],
    *,
    call_llm: Callable[[str], str],
    max_examples: int = 8,
    max_chars_each: int = 280,
    extra_stop: Iterable[str] | None = None,
) -> tuple[str, list[str]]:
    """有 LLM：读若干代表句，输出简短主题名（中文优先 2–8 字）。"""
    kws = _tfidf_keywords(docs, top_k=8, extra_stop=extra_stop)
    examples = []
    for d in docs[:max_examples]:
        t = re.sub(r"\s+", " ", (d or "").strip())
        if t:
            examples.append(t[:max_chars_each])
    prompt = (
        "你是主题归纳助手。下面是同一聚类的若干文档摘录与关键词。\n"
        "任务：用中文起一个能区分该簇的主题名（2～10 个字）。\n"
        "要求：\n"
        "1. 必须抓住簇内反复出现的具体现象/场景/情绪（如隧道白光、手术室出体、溺水、车祸、亲友重逢、黑暗虚空）；\n"
        "2. 禁止空泛名：濒死体验、NDE、综合、其他、经历、故事、灵魂之旅；\n"
        "3. 只输出主题名一行，不要引号、不要解释。\n\n"
        f"关键词：{', '.join(kws)}\n\n"
        "摘录：\n" + "\n---\n".join(f"- {e}" for e in examples)
    )
    raw = (call_llm(prompt) or "").strip().splitlines()[0].strip().strip("「」\"'")
    # 过长则截断
    if len(raw) > 16:
        raw = raw[:16]
    if not raw:
        return label_cluster_extractive(docs)
    return raw, kws


def discover_topics(
    matrix: np.ndarray,
    documents: Sequence[str],
    *,
    meta: Sequence[dict] | None = None,
    n_clusters: int | None = None,
    algorithm: str = "kmeans",
    exemplars_per_cluster: int = 5,
    labeler: str = "tfidf",  # tfidf | llm
    call_llm: Callable[[str], str] | None = None,
    extra_stop: Iterable[str] | None = None,
) -> TopicDiscoveryResult:
    """通用入口：向量 + 原文 → 聚类 + 自动起名。"""
    if len(matrix) != len(documents):
        raise ValueError("matrix 行数必须等于 documents 数")
    meta = list(meta) if meta is not None else [{} for _ in documents]
    labels, algo_name = cluster_embeddings(
        matrix, n_clusters=n_clusters, algorithm=algorithm,
    )
    by_c: dict[int, list[int]] = defaultdict(list)
    for i, lab in enumerate(labels):
        by_c[int(lab)].append(i)

    clusters: list[ClusterLabel] = []
    for cid in sorted(by_c.keys(), key=lambda c: -len(by_c[c])):
        idxs = by_c[cid]
        docs = [documents[i] for i in idxs]
        if labeler == "llm" and call_llm is not None:
            title, kws = label_cluster_with_llm(
                docs, call_llm=call_llm, extra_stop=extra_stop,
            )
            method = "llm"
        else:
            title, kws = label_cluster_extractive(docs, extra_stop=extra_stop)
            method = "tfidf"
        # 代表：靠近簇中心
        sub = l2_normalize(matrix[idxs])
        center = sub.mean(axis=0)
        center = center / (np.linalg.norm(center) + 1e-9)
        sims = sub @ center
        order = np.argsort(-sims)[:exemplars_per_cluster]
        exemplars = []
        for j in order:
            i = idxs[int(j)]
            item = dict(meta[i]) if meta[i] else {}
            item["text"] = (documents[i] or "")[:240]
            item["sim_to_center"] = round(float(sims[int(j)]), 4)
            exemplars.append(item)
        clusters.append(ClusterLabel(
            cluster_id=cid,
            size=len(idxs),
            title=title,
            keywords=kws,
            exemplars=exemplars,
            cohesion=round(_cohesion(matrix, idxs), 4),
            method=method,
        ))

    return TopicDiscoveryResult(
        n_docs=len(documents),
        n_clusters=len(by_c),
        labels=labels.tolist(),
        clusters=clusters,
        algorithm=algo_name,
        note="标题由聚类后自动生成；升格为正式 ontology 前需人工抽检",
    )


def result_to_dict(result: TopicDiscoveryResult) -> dict[str, Any]:
    return {
        "n_docs": result.n_docs,
        "n_clusters": result.n_clusters,
        "algorithm": result.algorithm,
        "note": result.note,
        "clusters": [asdict(c) for c in result.clusters],
        # labels 可能很长，默认不塞满；调用方需要可自行取 result.labels
    }
