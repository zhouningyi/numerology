"""API 用量记账：把每次调用的 token 数落盘，供成本复盘。

此前所有脚本都没记 response.usage，导致只能按字符数反推估算账单
（估出 $20–25，实际反推约 $13，误差很大）。记账后可出精确的分任务报表。

写 data/audits/api_usage.jsonl（追加式，并发安全）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

USAGE_PATH = Path("data/audits/api_usage.jsonl")
_LOCK = Lock()

# USD / 1M tokens。价格会变，报表以此表为准并在输出中标注版本。
PRICING = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    return prompt_tokens / 1e6 * price_in + completion_tokens / 1e6 * price_out


def record(model: str, usage, task: str = "", meta: dict | None = None) -> None:
    """usage 为 OpenAI 响应的 .usage 对象；拿不到就静默跳过，不影响主流程。"""
    if usage is None or os.environ.get("NUMEROLOGY_DISABLE_USAGE_LOG"):
        return
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "task": task or os.environ.get("NUMEROLOGY_TASK", ""),
        "model": model,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost_usd": round(estimate_cost(model, prompt, completion), 6),
    }
    if meta:
        row["meta"] = meta
    try:
        with _LOCK:
            USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with USAGE_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 记账失败不该中断正在跑的批量任务


def summarize(path: Path | None = None) -> dict:
    """按任务与模型汇总用量与成本。"""
    path = path or USAGE_PATH
    if not path.exists():
        return {"calls": 0, "total_cost": 0.0, "by_task": {}, "by_model": {}}
    by_task: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    calls = 0
    total = 0.0
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        calls += 1
        total += row.get("cost_usd", 0.0)
        for bucket, key in ((by_task, row.get("task") or "(未命名)"),
                            (by_model, row.get("model") or "(未知)")):
            entry = bucket.setdefault(
                key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
            )
            entry["calls"] += 1
            entry["prompt_tokens"] += row.get("prompt_tokens", 0)
            entry["completion_tokens"] += row.get("completion_tokens", 0)
            entry["cost_usd"] = round(entry["cost_usd"] + row.get("cost_usd", 0.0), 6)
    return {
        "calls": calls,
        "total_cost": round(total, 4),
        "by_task": dict(sorted(by_task.items(), key=lambda kv: -kv[1]["cost_usd"])),
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["cost_usd"])),
    }
