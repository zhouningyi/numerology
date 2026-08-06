#!/usr/bin/env python3
"""标准词表去重：合并归并残留的同义项。

分批 map-reduce 无法发现跨批重复——不同批各自产出"隧道/通道与向光移动"
和"隧道／通道感受"，末轮批次里才碰面。词表规模（约百项）能一次塞进上下文，
所以这里让模型通读全表、只做合并判断，不重新命名、不改判据。

保守起见：只合并**明确同义**的项，频次相加、成员样例合并、保留高频项的名称。
输出覆盖 taxonomy_draft.yaml（原文件备份为 .predupe.bak）。
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

PROMPT = """下面是一份濒死体验现象词表，每行 `#编号 名称 (命中次数)`。
找出其中**说的是同一个现象**的条目，把它们分为一组。

规则：
- 只合并明确同义的（"隧道/通道与向光移动" 与 "隧道／通道感受" 是同一现象）；
- 粒度不同但确为不同现象的**不要**合并
  （"看到光" 与 "被光吸纳"、"遇见已故亲人" 与 "遇见宗教人物" 都要分开）；
- 只需列出需要合并的组，单独成立的条目不用列出；
- 每组给出组内所有编号，以及应保留的名称（选表意最准确的一个）。

只输出 JSON：{"groups": [{"ids": [1, 5], "name": "保留的名称"}]}"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    items = list(data["phenomena"].items())
    listing = "\n".join(
        f"#{i + 1} {spec['name']} ({spec.get('approx_freq', 0)})"
        for i, (_, spec) in enumerate(items)
    )
    logger.info(f"词表 {len(items)} 项，交由 {args.model} 判定同义组")
    if args.dry_run:
        print(listing[:1500])
        return

    load_dotenv()
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=180.0, max_retries=3)
    kwargs = {
        "model": args.model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": listing},
        ],
    }
    if args.model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = args.reasoning_effort
    response = client.chat.completions.create(**kwargs)
    record_usage(args.model, getattr(response, "usage", None), task="dedupe_taxonomy")
    groups = json.loads(response.choices[0].message.content).get("groups", []) or []

    merged_into: dict[int, int] = {}   # 成员位置 → 组长位置
    keep_name: dict[int, str] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        ids = [
            int(str(i).lstrip("#")) - 1
            for i in (group.get("ids") or [])
            if str(i).lstrip("#").isdigit() and 1 <= int(str(i).lstrip("#")) <= len(items)
        ]
        if len(ids) < 2:
            continue
        # 组长取频次最高者，名称用模型给的（缺省时保留组长原名）
        leader = max(ids, key=lambda i: items[i][1].get("approx_freq", 0))
        keep_name[leader] = str(group.get("name") or items[leader][1]["name"]).strip()
        for i in ids:
            if i != leader:
                merged_into[i] = leader

    result: dict[str, dict] = {}
    for i, (key, spec) in enumerate(items):
        if i in merged_into:
            continue
        spec = dict(spec)
        if i in keep_name:
            spec["name"] = keep_name[i]
        absorbed = [j for j, leader in merged_into.items() if leader == i]
        for j in absorbed:
            other = items[j][1]
            spec["approx_freq"] = spec.get("approx_freq", 0) + other.get("approx_freq", 0)
            spec["seed_members"] = (spec.get("seed_members") or []) + (other.get("seed_members") or [])
            spec.setdefault("merged_from", []).append(items[j][0])
        spec["seed_members"] = (spec.get("seed_members") or [])[:12]
        result[key] = spec

    DRAFT.with_suffix(".yaml.predupe.bak").write_text(
        DRAFT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    data["phenomena"] = dict(
        sorted(result.items(), key=lambda kv: -kv[1].get("approx_freq", 0))
    )
    data["deduped_at"] = datetime.now(timezone.utc).isoformat()
    data["dedupe_groups"] = len([g for g in groups if isinstance(g, dict)])
    DRAFT.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    logger.info(f"合并 {len(merged_into)} 项，词表 {len(items)} → {len(result)} 项")


if __name__ == "__main__":
    main()
