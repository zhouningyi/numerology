#!/usr/bin/env python3
"""低信度标签的判据修订：按成员实际外延决定 保留/收紧/拆分/删除。

信度不足有三种病因，只补正反例治不了后两种：
- 判据含糊：外延对但话说得虚 → 补正反例（tighten）
- 外延混杂：把两件事装进一个标签（"遇见存有"混入"感到爱充满房间"）→ 拆分（split）
- 根本不该存在：抽取时漏进来的医疗经过（胸痛、呼吸困难）→ 删除（drop）

判断依据是该标签归并时吸收的成员短语——那才是它的真实外延，
比标签名和原判据都可靠。修订结果写回 taxonomy_draft.yaml（备份 .prerefine.bak）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from numerology.api_usage import record as record_usage
from scripts.nde.translate_nderf import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DRAFT = Path("data/processed/nderf/taxonomy/taxonomy_draft.yaml")
RELIABILITY = Path("data/audits/taxonomy_reliability.json")

PROMPT = """你在修订一个濒死体验现象标签，它的标注信度不达标（两次独立标注结果不一致）。
给你标签名、现有判据，以及归并时吸收的成员短语（**这些才是它的真实外延**）。

先判断病因，再给处置：
- "drop"：这根本不是濒死体验现象，而是医疗/事故经过（发病、症状、抢救）、
  旁人行为或叙述行为 → 删除；
- "split"：成员里混着两件不同的事（例如"遇见某个存有"与"感到爱充满空间"，
  前者要有对象、后者是纯情感）→ 拆成 2 个边界清楚的标签；
- "tighten"：外延一致但判据说得太虚 → 保留，把判据写清楚。

tighten 与 split 的每个标签都必须给：
- criterion：一句话判据，说明"必须出现什么才算命中"；
- positive：一个典型正例（英文短句）；
- negative：一个**容易误判**的反例，并说明为何不算。

只输出 JSON：
{"action":"drop|split|tighten","reason":"...",
 "labels":[{"key":"snake_case","name":"中文名","criterion":"...","positive":"...","negative":"..."}]}
（drop 时 labels 为空数组）"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--alpha-max", type=float, default=0.80)
    parser.add_argument("--min-rate", type=float, default=0.05,
                        help="只修中高频标签；低频的 α 不可靠，需另行过采样测")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    phenomena = data["phenomena"]
    per_label = json.loads(RELIABILITY.read_text(encoding="utf-8"))["per_label"]
    targets = [
        key for key, spec in phenomena.items()
        if (stat := per_label.get(key))
        and stat.get("alpha") is not None
        and stat["alpha"] < args.alpha_max
        and stat["hits_first"] / max(1, stat["n"]) >= args.min_rate
    ]
    logger.info(f"待修订 {len(targets)} 个标签（α<{args.alpha_max} 且命中率≥{args.min_rate:.0%}）")
    if args.dry_run:
        for key in targets:
            print(f"  α={per_label[key]['alpha']:.2f}  {phenomena[key]['name'][:40]}")
        return

    load_dotenv()
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=180.0, max_retries=3)

    def refine(key: str) -> dict:
        spec = phenomena[key]
        payload = (
            f"标签名：{spec['name']}\n"
            f"现有判据：{spec.get('criterion', '（无）')}\n"
            f"标注信度 α={per_label[key]['alpha']:.2f}"
            f"（{per_label[key]['hits_first']}/{per_label[key]['n']} 篇命中）\n"
            f"成员短语：\n" +
            "\n".join(f"- {m}" for m in (spec.get("seed_members") or [])[:14])
        )
        kwargs = {
            "model": args.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": payload},
            ],
        }
        if args.model.startswith("gpt-5"):
            kwargs["reasoning_effort"] = args.reasoning_effort
        response = client.chat.completions.create(**kwargs)
        record_usage(args.model, getattr(response, "usage", None), task="refine_criteria")
        return json.loads(response.choices[0].message.content)

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(refine, k): k for k in targets}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{key} 修订失败: {str(exc)[:90]}")

    stats = {"drop": 0, "split": 0, "tighten": 0, "skip": 0}
    updated = dict(phenomena)
    for key, result in results.items():
        action = str(result.get("action") or "").strip()
        labels = [l for l in (result.get("labels") or []) if isinstance(l, dict)]
        original = updated.get(key, {})
        if action == "drop":
            updated.pop(key, None)
            stats["drop"] += 1
            logger.info(f"删除：{original.get('name','')[:30]} —— {result.get('reason','')[:50]}")
        elif action == "split" and len(labels) >= 2:
            updated.pop(key, None)
            share = max(1, original.get("approx_freq", 0) // len(labels))
            for label in labels:
                new_key = str(label.get("key") or "").strip()
                if not new_key:
                    continue
                updated[new_key] = {
                    "name": label.get("name") or new_key,
                    "criterion": label.get("criterion", ""),
                    "positive": label.get("positive", ""),
                    "negative": label.get("negative", ""),
                    "approx_freq": share,
                    "seed_members": (original.get("seed_members") or [])[:8],
                    "status": "candidate",
                    "split_from": key,
                }
            stats["split"] += 1
        elif labels:
            label = labels[0]
            updated[key] = {
                **original,
                "criterion": label.get("criterion", original.get("criterion", "")),
                "positive": label.get("positive", ""),
                "negative": label.get("negative", ""),
                "refined": True,
            }
            stats["tighten"] += 1
        else:
            stats["skip"] += 1

    DRAFT.with_suffix(".yaml.prerefine.bak").write_text(
        DRAFT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    data["phenomena"] = dict(
        sorted(updated.items(), key=lambda kv: -kv[1].get("approx_freq", 0))
    )
    data["refined_at"] = datetime.now(timezone.utc).isoformat()
    DRAFT.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    logger.info(
        f"删除 {stats['drop']}、拆分 {stats['split']}、收紧 {stats['tighten']}、跳过 {stats['skip']}"
        f"；词表 {len(phenomena)} → {len(updated)} 项"
    )


if __name__ == "__main__":
    main()
