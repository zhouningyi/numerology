#!/usr/bin/env python3
"""濒死叙述自动主题发现：嵌入聚类 + 自动起名（几个字总结）。

通用解在 numerology/topic_discovery.py；本脚本只负责：
- 读 NDE 嵌入与 experiences 正文
- 聚类
- TF-IDF 或本地 Ollama 起名
- 写出审计 JSON，供人工升格到 motifs/phenomena

示例：
  python3 -m scripts.nde.discover_nde_topics --k 24
  python3 -m scripts.nde.discover_nde_topics --k 20 --labeler ollama --model qwen3:8b
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from numerology.topic_discovery import discover_topics, result_to_dict

EMBED_DIR = Path("data/processed/embeddings")
MATRIX = EMBED_DIR / "matrix.npy"
META = EMBED_DIR / "meta.jsonl"
EXPERIENCES = Path("data/processed/nderf/experiences.jsonl")
AUDITS = Path("data/audits")


def _load_nde_corpus() -> tuple[np.ndarray, list[str], list[dict]]:
    matrix = np.load(MATRIX)
    meta = [
        json.loads(line)
        for line in META.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(matrix) != len(meta):
        raise SystemExit(f"matrix({len(matrix)}) 与 meta({len(meta)}) 行数不一致")

    by_slug = {}
    if EXPERIENCES.exists():
        for line in EXPERIENCES.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            by_slug[row["slug"]] = row

    indices = []
    docs = []
    metas = []
    for i, m in enumerate(meta):
        if m.get("source") != "nde":
            continue
        slug = m.get("ref") or ""
        rec = by_slug.get(slug, {})
        text = (rec.get("description") or m.get("excerpt") or "").strip()
        if len(text) < 40:
            continue
        indices.append(i)
        docs.append(text)
        metas.append({
            "slug": slug,
            "title": m.get("title") or rec.get("title") or slug,
            "url": m.get("url") or f"/nde/experience/{slug}",
        })
    if not indices:
        raise SystemExit("没有可用的 NDE 文档（检查 embeddings 与 experiences）")
    return matrix[np.array(indices)], docs, metas


def _ollama_labeler(model: str):
    import requests

    def call(prompt: str) -> str:
        r = requests.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.2, "num_predict": 64},
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")

    return call


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, default=24, help="聚类数（主题数）")
    parser.add_argument(
        "--algorithm", default="kmeans",
        choices=["kmeans", "minibatch", "agglomerative"],
    )
    parser.add_argument(
        "--labeler", default="tfidf",
        choices=["tfidf", "ollama"],
        help="tfidf=关键词拼标题；ollama=本地模型用几个字总结",
    )
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--limit", type=int, default=None, help="调试时限制文档数")
    args = parser.parse_args()

    matrix, docs, metas = _load_nde_corpus()
    if args.limit:
        matrix, docs, metas = matrix[: args.limit], docs[: args.limit], metas[: args.limit]

    call_llm = _ollama_labeler(args.model) if args.labeler == "ollama" else None
    # 濒死叙述高频粘合词：不做主题区分
    nde_stop = {
        "remember", "life", "told", "body", "saw", "see", "felt", "feel",
        "experience", "experiences", "went", "come", "came", "time", "back",
        "people", "person", "thing", "things", "know", "knew", "think",
        "thought", "seemed", "started", "around", "something", "someone",
        "everything", "anything", "nothing", "still", "also", "really",
        "hospital", "doctor", "room", "bed", "years", "old", "father",
        "mother",  # 保留在关键词里也可；先当粘合词避免霸榜
    }
    result = discover_topics(
        matrix,
        docs,
        meta=metas,
        n_clusters=args.k,
        algorithm=args.algorithm,
        labeler="llm" if args.labeler == "ollama" else "tfidf",
        call_llm=call_llm,
        extra_stop=nde_stop,
    )

    payload = result_to_dict(result)
    payload["checked_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload["labeler"] = args.labeler
    payload["how_to_use"] = {
        "promote_to_motif": "把稳定 cluster 的 keywords/title 写进 numerology/nde/motifs.yaml",
        "promote_to_phenomenon": "仅当能映射到问卷题时才写 phenomena.yaml",
        "doc_assignment": "labels 未写入本文件以控制体积；需要可加 --dump-labels",
    }

    # 精简 exemplars 文本
    for c in payload["clusters"]:
        for ex in c.get("exemplars") or []:
            if "text" in ex:
                ex["text"] = ex["text"][:180]

    AUDITS.mkdir(parents=True, exist_ok=True)
    stamp = payload["checked_at"].replace(":", "").replace("+00:00", "Z")
    path = AUDITS / f"nde_topics_{stamp}.json"
    latest = AUDITS / "nde_topics_latest.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")

    summary = {
        "path": str(path),
        "latest": str(latest),
        "n_docs": result.n_docs,
        "n_clusters": result.n_clusters,
        "algorithm": result.algorithm,
        "topics": [
            {
                "id": c.cluster_id,
                "size": c.size,
                "title": c.title,
                "keywords": c.keywords[:5],
                "cohesion": c.cohesion,
            }
            for c in result.clusters[:15]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
