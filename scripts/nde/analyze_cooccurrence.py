#!/usr/bin/env python3
"""现象共现分析：哪些现象倾向于一起出现，哪些互斥。

只用 statistical 层标签（信度达标），因为共现分析对标注噪声极敏感：
两个各有 30% 误标率的标签，其相关性估计基本是噪声的相关性。

统计量：
- 支持度 support：两现象同时出现的篇数占比
- 提升度 lift：观察共现 / 独立假设下的期望共现（>1 倾向同现，<1 倾向互斥）
- φ 系数：二元变量的相关系数，附 χ² 检验与 BH-FDR 校正

FDR 校正是必须的：19 个标签有 171 个配对，α=0.05 下期望约 9 个假阳性。
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import yaml

LABELS = Path("data/processed/nderf/taxonomy/labels.jsonl")
DRAFT = Path("data/processed/nderf/taxonomy/taxonomy_draft.yaml")
AUDITS = Path("data/audits")


def chi2_p_value(chi2: float) -> float:
    """自由度 1 的卡方上尾概率：p = erfc(sqrt(chi2/2))。"""
    return math.erfc(math.sqrt(max(chi2, 0.0) / 2.0))


def benjamini_hochberg(pvals: list[float], alpha: float = 0.05) -> list[bool]:
    """BH-FDR：返回每项是否在 FDR≤alpha 下显著。"""
    indexed = sorted(enumerate(pvals), key=lambda kv: kv[1])
    n = len(pvals)
    significant = [False] * n
    max_rank = -1
    for rank, (_, p) in enumerate(indexed, start=1):
        if p <= alpha * rank / n:
            max_rank = rank
    for rank, (idx, _) in enumerate(indexed, start=1):
        if rank <= max_rank:
            significant[idx] = True
    return significant


def pair_stats(a: set, b: set, total: int) -> dict:
    n11 = len(a & b)
    n10 = len(a - b)
    n01 = len(b - a)
    n00 = total - n11 - n10 - n01
    pa, pb = len(a) / total, len(b) / total
    expected = pa * pb * total
    lift = (n11 / expected) if expected > 0 else 0.0
    denom = math.sqrt(max(1e-9, (n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00)))
    phi = (n11 * n00 - n10 * n01) / denom
    chi2 = total * phi * phi
    return {
        "both": n11, "support": round(n11 / total, 4),
        "lift": round(lift, 3), "phi": round(phi, 3),
        "chi2": round(chi2, 2), "p": chi2_p_value(chi2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-support", type=int, default=20,
                        help="共现篇数下限，太小的配对估计不稳")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    draft = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))["phenomena"]
    names = {k: v["name"] for k, v in draft.items()}
    stat_keys = {k for k, v in draft.items() if v.get("tier") == "statistical"}

    by_label: dict[str, set] = {}
    docs = set()
    for line in LABELS.open(encoding="utf-8"):
        row = json.loads(line)
        docs.add(row["slug"])
        for key in row.get("hits", {}):
            if key in stat_keys:
                by_label.setdefault(key, set()).add(row["slug"])
    total = len(docs)
    counts = Counter({k: len(v) for k, v in by_label.items()})
    print(f"样本 {total} 篇，可统计标签 {len(by_label)} 个\n")
    print("现象频次：")
    for key, n in counts.most_common():
        print(f"  {n:>5} ({n/total:>5.1%})  {names.get(key, key)[:36]}")

    pairs = []
    for a, b in combinations(sorted(by_label), 2):
        stat = pair_stats(by_label[a], by_label[b], total)
        if stat["both"] < args.min_support:
            continue
        pairs.append({"a": a, "b": b, **stat})
    if pairs:
        flags = benjamini_hochberg([p["p"] for p in pairs])
        for pair, sig in zip(pairs, flags):
            pair["fdr_significant"] = sig

    strong = sorted([p for p in pairs if p["fdr_significant"]], key=lambda p: -p["lift"])
    inverse = sorted([p for p in pairs if p["fdr_significant"]], key=lambda p: p["lift"])
    print(f"\n共现配对 {len(pairs)} 个，FDR<0.05 显著 {sum(p['fdr_significant'] for p in pairs)} 个")
    print(f"\n最强同现（lift 高 = 倾向一起出现）：")
    for p in strong[: args.top]:
        print(f"  lift={p['lift']:>5.2f} φ={p['phi']:>6.3f} 共{p['both']:>4}篇  "
              f"{names.get(p['a'],p['a'])[:20]} × {names.get(p['b'],p['b'])[:20]}")
    print(f"\n最强互斥（lift 低 = 倾向不同时出现）：")
    for p in inverse[:8]:
        print(f"  lift={p['lift']:>5.2f} φ={p['phi']:>6.3f} 共{p['both']:>4}篇  "
              f"{names.get(p['a'],p['a'])[:20]} × {names.get(p['b'],p['b'])[:20]}")

    AUDITS.mkdir(parents=True, exist_ok=True)
    out = AUDITS / "nde_cooccurrence.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_docs": total, "labels": dict(counts),
        "pairs": sorted(pairs, key=lambda p: -p["lift"]),
        "note": "只含 statistical 层标签；p 值经 BH-FDR 校正，未显著者不应解读",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告 -> {out}")


if __name__ == "__main__":
    main()
