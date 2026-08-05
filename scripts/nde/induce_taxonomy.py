#!/usr/bin/env python3
"""从叙述自底向上归纳现象体系（开放编码 → map-reduce 归并 → 信度 → 计数）。

问卷现象的天花板是 NDERF 问卷设计者当年的想象力；关键词母题只能抓
已知的东西。本管线不给模型任何预设清单，让它从叙述里报现象，再把
几万条自由表述归并成可计数的标准词表。

五阶段（各自落盘、可断点续跑）：
  extract      样本分段开放抽取     → observations.jsonl
  merge        map-reduce 分层归并  → taxonomy_draft.yaml + merge_trace.jsonl
  reliability  双标注测信度         → data/audits/taxonomy_reliability.json
  label        全库固定词表标注     → labels.jsonl（可直接 count）
  residual     零覆盖文档回流       → 下一轮 extract 的输入

用法：
  python3 -m scripts.nde.induce_taxonomy --stage extract --sample 800
  python3 -m scripts.nde.induce_taxonomy --stage merge
  python3 -m scripts.nde.induce_taxonomy --stage reliability --sample 300
  python3 -m scripts.nde.induce_taxonomy --stage label
  python3 -m scripts.nde.induce_taxonomy --stage residual
任何阶段加 --dry-run 只做规划与成本预估，不调 API。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from numerology.nde.taxonomy import (
    agreement_report,
    audit_taxonomy,
    batched,
    dedupe_phrases,
    plan_merge_rounds,
    select_residual,
    split_paragraphs,
)
from scripts.nde.translate_nderf import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EXPERIENCES = Path("data/processed/nderf/experiences.jsonl")
OUT_DIR = Path("data/processed/nderf/taxonomy")
AUDITS = Path("data/audits")
OBSERVATIONS = OUT_DIR / "observations.jsonl"
DRAFT = OUT_DIR / "taxonomy_draft.yaml"
TRACE = OUT_DIR / "merge_trace.jsonl"
LABELS = OUT_DIR / "labels.jsonl"

# 抽取阶段刻意不给清单，只给"什么算现象"的判据，避免锚定到已有体系
EXTRACT_PROMPT = """这是一段濒死体验自述。列出其中**体验者报告的现象**。

什么算现象：体验中发生的知觉、感受、认知或遭遇（看到什么、感到什么、
明白了什么、遇到了谁、身体或时间感如何变化、之后有何转变）。
什么不算：医疗与事故背景（手术、车祸、抢救过程）、旁人行为、单纯的情节交代。

要求：
- 用简短的英文名词短语描述现象本身，去掉人称和具体人名地名
  （写 "being pulled toward light" 而不是 "John pulled me toward the light"）；
- 每个现象附一句**逐字引用**的原文作证据；
- 没有可报告的现象就返回空列表，不要硬凑。

只输出 JSON：{"phenomena": [{"phrase": "...", "evidence": "..."}]}"""

# 归并阶段携带频次：高频项若被并入宽泛父类，统计上会损失分辨率
MERGE_PROMPT = """下面是从濒死体验叙述中抽取的现象短语，格式为 `短语 (出现次数)`。
把**说的是同一件事**的短语合并成标准项。

规则：
- 表述不同但现象相同的合并（"being pulled" / "drawn toward" / "sucked into" → 同一项）；
- 现象不同的不要合并，哪怕主题相近（"看到光"与"光有意识"是两件事）；
- **保持具体，不要过度抽象**：不要合并成"感知体验""情绪变化"这种空壳；
- 出现次数高的短语优先保留其粒度，不要并入宽泛父类；
- 每个标准项：英文 key（snake_case）、中文名、一句判据、成员短语列表。

只输出 JSON：{"items": [{"key": "...", "name": "...", "criterion": "...", "members": ["..."]}]}"""

LABEL_PROMPT_TEMPLATE = """判断这段濒死体验叙述命中了下列哪些现象。

现象清单：
{catalog}

规则：
- 只标叙述中**明确表达**的，氛围相似、可以引申的一律不标；
- 每个命中项给一句逐字引用的原文证据；
- 命中 0 个是正常的。

只输出 JSON：{{"hits": {{"现象key": "证据句", ...}}}}"""


def _client():
    from openai import OpenAI

    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _chat_json(client, model: str, effort: str, system: str, user: str) -> dict:
    kwargs = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user[:48000]},
        ],
    }
    if model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = effort
    response = client.chat.completions.create(**kwargs)
    return json.loads(response.choices[0].message.content)


def load_experiences() -> list[dict]:
    return [json.loads(l) for l in EXPERIENCES.open(encoding="utf-8") if l.strip()]


def sample_docs(rows: list[dict], size: int, seed: int, exclude: set[str] | None = None) -> list[dict]:
    """固定 seed 抽样：挖掘集与保留集必须可复现且互斥。"""
    pool = [r for r in rows if r["slug"] not in (exclude or set())]
    pool = [r for r in pool if len(r.get("description", "")) >= 200]
    rng = random.Random(seed)
    return rng.sample(pool, min(size, len(pool)))


def _run_parallel(jobs, workers: int, on_result, desc: str = ""):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import Lock

    lock = Lock()
    done = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn): meta for fn, meta in jobs}
        for future in as_completed(futures):
            meta = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 —— 单项失败不中断批量
                with lock:
                    fail += 1
                logger.warning(f"{meta} 失败: {str(exc)[:100]}")
                continue
            with lock:
                on_result(meta, result)
                done += 1
                if done % 100 == 0:
                    logger.info(f"{desc} 进度 {done}/{len(futures)}（失败 {fail}）")
    logger.info(f"{desc} 完成 {done}，失败 {fail}")


# ── 阶段一：开放抽取 ────────────────────────────────────────────
def stage_extract(args) -> None:
    rows = load_experiences()
    done_slugs = set()
    if OBSERVATIONS.exists():
        for line in OBSERVATIONS.open(encoding="utf-8"):
            try:
                done_slugs.add(json.loads(line)["slug"])
            except (json.JSONDecodeError, KeyError):
                continue
    if args.residual_input and Path(args.residual_input).exists():
        wanted = {l.strip() for l in Path(args.residual_input).read_text().splitlines() if l.strip()}
        docs = [r for r in rows if r["slug"] in wanted and r["slug"] not in done_slugs]
        logger.info(f"残差轮：{len(docs)} 篇")
    else:
        docs = [d for d in sample_docs(rows, args.sample, args.seed) if d["slug"] not in done_slugs]
    units = [
        (doc, index, para)
        for doc in docs
        for index, para in enumerate(split_paragraphs(doc.get("description", "")))
    ]
    logger.info(f"待抽取 {len(docs)} 篇 / {len(units)} 段（模型 {args.model}）")
    if args.dry_run:
        chars = sum(len(p) for _, _, p in units)
        logger.info(f"预估输入 {chars/4/1e6:.2f}M tokens，约 ${chars/4/1e6*0.25:.2f}")
        return

    load_dotenv()
    client = _client()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OBSERVATIONS.open("a", encoding="utf-8")

    def make_job(doc, para, index):
        def run():
            return _chat_json(client, args.model, args.reasoning_effort, EXTRACT_PROMPT, para)
        return run, (doc["slug"], index)

    def on_result(meta, result):
        slug, index = meta
        for item in result.get("phenomena", []) or []:
            phrase = (item or {}).get("phrase", "").strip()
            if not phrase:
                continue
            out.write(json.dumps({
                "slug": slug, "para": index,
                "phrase": phrase, "evidence": (item.get("evidence") or "").strip(),
            }, ensure_ascii=False) + "\n")
        out.flush()

    jobs = [make_job(doc, para, index) for doc, index, para in units]
    _run_parallel(jobs, args.workers, on_result, "抽取")
    out.close()


# ── 阶段二：map-reduce 归并 ─────────────────────────────────────
def _require(path: Path, hint: str) -> None:
    if not path.exists() or not path.stat().st_size:
        raise SystemExit(f"缺少 {path}；请先运行：{hint}")


def stage_merge(args) -> None:
    _require(OBSERVATIONS, "--stage extract")
    observations = [json.loads(l) for l in OBSERVATIONS.open(encoding="utf-8") if l.strip()]
    phrases = dedupe_phrases([o["phrase"] for o in observations])
    plan = plan_merge_rounds(len(phrases), args.batch_size, args.compress, args.target)
    logger.info(f"原始短语 {len(observations)} 条，去重后 {len(phrases)} 项")
    for i, step in enumerate(plan, 1):
        logger.info(f"  第{i}轮：{step['input']} 项 / {step['batches']} 批 → 约 {step['expected']} 项")
    if args.dry_run:
        calls = sum(s["batches"] for s in plan)
        logger.info(f"共 {calls} 次调用，约 ${calls * 0.004:.2f}")
        return

    load_dotenv()
    client = _client()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    trace = TRACE.open("w", encoding="utf-8")
    # current：[(展示短语, 频次, 溯源成员)]
    current = [(p, n, [p]) for p, n in phrases]
    for round_index in range(1, len(plan) + 1):
        merged: list[tuple[str, int, list[str]]] = []
        batches = batched(current, args.batch_size)

        def make_job(batch, bi):
            listing = "\n".join(f"- {p} ({n})" for p, n, _ in batch)
            def run():
                return _chat_json(client, args.model, args.reasoning_effort, MERGE_PROMPT, listing)
            return run, bi

        results: dict[int, dict] = {}
        _run_parallel(
            [make_job(b, i) for i, b in enumerate(batches)],
            args.workers, lambda meta, res: results.__setitem__(meta, res),
            f"归并第{round_index}轮",
        )
        for bi, result in sorted(results.items()):
            lookup = {p: (n, members) for p, n, members in batches[bi]}
            for item in result.get("items", []) or []:
                key = (item or {}).get("key") or ""
                name = item.get("name") or key
                members = [m for m in (item.get("members") or []) if isinstance(m, str)]
                freq, lineage = 0, []
                for m in members:
                    hit = lookup.get(m)
                    if hit:
                        freq += hit[0]
                        lineage.extend(hit[1])
                if not key:
                    continue
                merged.append((f"{name}｜{item.get('criterion','')[:60]}", max(freq, 1), lineage or members))
                trace.write(json.dumps({
                    "round": round_index, "key": key, "name": name,
                    "criterion": item.get("criterion", ""), "members": members,
                    "lineage_size": len(lineage), "freq": freq,
                }, ensure_ascii=False) + "\n")
        trace.flush()
        logger.info(f"第{round_index}轮：{len(current)} → {len(merged)} 项")
        if not merged or len(merged) >= len(current):
            break
        current = merged
    trace.close()

    # 终版词表：末轮 trace 即为标准项
    last_round = 0
    items = []
    for line in TRACE.open(encoding="utf-8"):
        row = json.loads(line)
        if row["round"] > last_round:
            last_round, items = row["round"], []
        if row["round"] == last_round:
            items.append(row)
    catalog = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_phrases": len(phrases),
        "rounds": last_round,
        "phenomena": {
            row["key"]: {
                "name": row["name"],
                "criterion": row["criterion"],
                "seed_members": row["members"][:12],
                "approx_freq": row["freq"],
                "status": "candidate",
            }
            for row in items
        },
    }
    DRAFT.write_text(yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False), encoding="utf-8")
    logger.info(f"标准词表 {len(catalog['phenomena'])} 项 -> {DRAFT}")


def _catalog_text(draft: dict) -> str:
    return "\n".join(
        f"- {key}: {spec['name']} —— {spec.get('criterion','')}"
        for key, spec in draft["phenomena"].items()
    )


# ── 阶段三：双标注信度 ──────────────────────────────────────────
def stage_reliability(args) -> None:
    _require(DRAFT, "--stage merge")
    draft = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    rows = load_experiences()
    docs = sample_docs(rows, args.sample, args.seed + 991)  # 与挖掘集不同 seed
    logger.info(f"信度样本 {len(docs)} 篇 × 2 轮（模型 {args.model}）")
    if args.dry_run:
        chars = sum(len(d.get("description", "")[:8000]) for d in docs) * 2
        logger.info(f"预估约 ${chars/4/1e6*0.25:.2f}")
        return

    load_dotenv()
    client = _client()
    prompt = LABEL_PROMPT_TEMPLATE.format(catalog=_catalog_text(draft))
    passes: list[dict[str, set]] = []
    for run_index in (1, 2):
        result: dict[str, set] = {}

        def make_job(doc):
            def run():
                return _chat_json(client, args.model, args.reasoning_effort, prompt,
                                  doc.get("description", "")[:8000])
            return run, doc["slug"]

        _run_parallel(
            [make_job(d) for d in docs], args.workers,
            lambda slug, res: result.__setitem__(slug, set((res.get("hits") or {}).keys())),
            f"标注第{run_index}轮",
        )
        passes.append(result)

    report = agreement_report(passes[0], passes[1], list(draft["phenomena"]))
    usable = [k for k, v in report.items() if (v["alpha"] or 0) >= 0.80]
    weak = [k for k, v in report.items() if v["alpha"] is not None and v["alpha"] < 0.67]
    AUDITS.mkdir(parents=True, exist_ok=True)
    path = AUDITS / "taxonomy_reliability.json"
    path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_docs": len(docs), "model": args.model,
        "usable_labels": usable, "weak_labels": weak, "per_label": report,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"α≥0.80 可用 {len(usable)} 项；α<0.67 需修判据 {len(weak)} 项 -> {path}")
    if weak:
        logger.warning(f"信度不足：{weak[:10]} —— 建议改判据后重测，不要直接进 label 阶段")


# ── 阶段四：全库标注与计数 ──────────────────────────────────────
def stage_label(args) -> None:
    _require(DRAFT, "--stage merge")
    draft = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    rows = load_experiences()
    done = set()
    if LABELS.exists():
        for line in LABELS.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["slug"])
            except (json.JSONDecodeError, KeyError):
                continue
    todo = [r for r in rows if r["slug"] not in done and len(r.get("description", "")) >= 200]
    if args.limit:
        todo = todo[: args.limit]
    logger.info(f"待标注 {len(todo)} 篇 / 词表 {len(draft['phenomena'])} 项")
    if args.dry_run:
        chars = sum(len(d.get("description", "")[:8000]) for d in todo)
        logger.info(f"预估约 ${chars/4/1e6*0.25:.2f}")
        return

    load_dotenv()
    client = _client()
    prompt = LABEL_PROMPT_TEMPLATE.format(catalog=_catalog_text(draft))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = LABELS.open("a", encoding="utf-8")

    def make_job(doc):
        def run():
            return _chat_json(client, args.model, args.reasoning_effort, prompt,
                              doc.get("description", "")[:8000])
        return run, doc["slug"]

    def on_result(slug, result):
        hits = {k: v for k, v in (result.get("hits") or {}).items() if k in draft["phenomena"]}
        out.write(json.dumps({"slug": slug, "hits": hits}, ensure_ascii=False) + "\n")
        out.flush()

    _run_parallel([make_job(d) for d in todo], args.workers, on_result, "标注")
    out.close()
    report_counts(draft)


def report_counts(draft: dict | None = None) -> None:
    """标注结果 → 计数与体系体检。"""
    _require(LABELS, "--stage label")
    draft = draft or yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    doc_labels = {}
    for line in LABELS.open(encoding="utf-8"):
        row = json.loads(line)
        doc_labels[row["slug"]] = set(row.get("hits", {}))
    audit = audit_taxonomy(doc_labels)
    AUDITS.mkdir(parents=True, exist_ok=True)
    path = AUDITS / "taxonomy_counts.json"
    named = {
        draft["phenomena"].get(k, {}).get("name", k): v
        for k, v in audit["counts"].items()
    }
    path.write_text(json.dumps({**audit, "named_counts": named},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"覆盖率 {audit['coverage']:.0%}，标签 {audit['labels']} 项")
    if audit["too_broad"]:
        logger.warning(f"过宽（>60%）需拆分：{audit['too_broad'][:5]}")
    if audit["redundant_pairs"]:
        logger.warning(f"冗余对（Jaccard≥0.8）需合并：{audit['redundant_pairs'][:3]}")
    logger.info(f"计数报告 -> {path}")


# ── 阶段五：残差回流 ────────────────────────────────────────────
def stage_residual(args) -> None:
    _require(LABELS, "--stage label")
    doc_labels = {}
    for line in LABELS.open(encoding="utf-8"):
        row = json.loads(line)
        doc_labels[row["slug"]] = set(row.get("hits", {}))
    residual = select_residual(doc_labels)
    path = OUT_DIR / "residual_slugs.txt"
    path.write_text("\n".join(residual), encoding="utf-8")
    logger.info(f"零覆盖 {len(residual)} 篇（{len(residual)/max(1,len(doc_labels)):.1%}）-> {path}")
    logger.info(f"下一轮：--stage extract --residual-input {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True,
                        choices=["extract", "merge", "reliability", "label", "residual", "counts"])
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--sample", type=int, default=800)
    parser.add_argument("--seed", type=int, default=20260806)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--compress", type=int, default=10)
    parser.add_argument("--target", type=int, default=120)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--residual-input", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    {
        "extract": stage_extract, "merge": stage_merge,
        "reliability": stage_reliability, "label": stage_label,
        "residual": stage_residual, "counts": lambda a: report_counts(),
    }[args.stage](args)


if __name__ == "__main__":
    main()
