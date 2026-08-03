#!/usr/bin/env python3
"""构建语义检索向量库：NDE 案例 + 华严经段落 → OpenAI 嵌入 → 本地索引。

- NDE：标题 + 叙述（有中文译文时并入，改善中文查询召回）
- 华严经：原文段落 + 现代释译段落（如已生成）
- 模型 text-embedding-3-small（1536 维），全量重建成本约 $0.15
输出 data/processed/embeddings/{matrix.npy, meta.jsonl}
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np

from numerology.nde.search import EMBED_DIR, normalize
from translate_nderf import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EXPERIENCES = Path("data/processed/nderf/experiences.jsonl")
TRANSLATIONS = Path("data/processed/nderf/translations.jsonl")
HUAYAN_LAYERS = [
    ("原文", Path("data/processed/canon/layers/huayan_t0279_layers.jsonl")),
    ("现代释译", Path("data/processed/canon/layers/huayan_t0279_modern_layers.jsonl")),
    ("现代释译", Path("data/processed/canon/layers/huayan_t0279_generated_layers.jsonl")),
]
# 嵌入模型上限 8192 tokens；中文每字可达 1-2 token，4000 字符留足余量
MAX_CHARS = 4000
EMBED_MODEL = "text-embedding-3-small"


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def collect_documents() -> list[dict]:
    docs: list[dict] = []
    translations = {r["slug"]: r for r in _jsonl(TRANSLATIONS)}
    for record in _jsonl(EXPERIENCES):
        text = f"{record['title']}\n{record['description']}"
        zh = translations.get(record["slug"], {}).get("zh", "")
        if zh:
            text += "\n" + zh
        docs.append({
            "source": "nde",
            "ref": record["slug"],
            "title": record["title"],
            "url": f"/nde/experience/{record['slug']}",
            "excerpt": record["description"][:160],
            "text": text[:MAX_CHARS],
        })
    for layer_name, path in HUAYAN_LAYERS:
        for seg in _jsonl(path):
            if seg.get("layer") not in ("原文", "现代释译"):
                continue
            text = seg.get("text", "")
            if len(text) < 30:
                continue
            chapter = seg.get("chapter")
            docs.append({
                "source": "huayan",
                "ref": f"huayan_t0279#{chapter}/{seg.get('segment_index')}",
                "title": f"华严经 第{chapter}章 {seg.get('chapter_title') or ''}（{layer_name}）",
                "url": f"/canon/huayan_t0279?chapter={chapter}",
                "excerpt": text[:160],
                "text": text[:MAX_CHARS],
            })
    return docs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    docs = collect_documents()
    est_tokens = sum(len(d["text"]) for d in docs) / 4
    logger.info(f"文档 {len(docs)} 条（NDE {sum(d['source']=='nde' for d in docs)}，"
                f"华严 {sum(d['source']=='huayan' for d in docs)}），"
                f"预估 {est_tokens/1e6:.1f}M tokens ≈ ${est_tokens/1e6*0.02:.2f}")
    if args.dry_run:
        return

    import os
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    vectors: list[list[float]] = []
    for start in range(0, len(docs), args.batch_size):
        batch = docs[start : start + args.batch_size]
        response = client.embeddings.create(
            model=EMBED_MODEL, input=[d["text"] for d in batch]
        )
        vectors.extend(item.embedding for item in response.data)
        if (start // args.batch_size) % 10 == 0:
            logger.info(f"进度 {start + len(batch)}/{len(docs)}")

    matrix = normalize(np.array(vectors, dtype=np.float32))
    EMBED_DIR.mkdir(parents=True, exist_ok=True)
    np.save(EMBED_DIR / "matrix.npy", matrix)
    with (EMBED_DIR / "meta.jsonl").open("w", encoding="utf-8") as handle:
        for doc in docs:
            meta = {k: v for k, v in doc.items() if k != "text"}
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
    logger.info(f"索引完成：{matrix.shape} -> {EMBED_DIR}")


if __name__ == "__main__":
    main()
