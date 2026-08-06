#!/usr/bin/env python3
"""用多个模型对古籍现代译文做逐段、单调、可审计的字句级对齐。

本脚本不使用字数比例生成最终对应关系。流程是：

1. 本地小模型提出每个现代段对应的原文段号候选；
2. 本地大模型独立复核；
3. 两者冲突时，可调用 Gemini 作为第三方裁决；
4. 结果保留每个模型的原始 JSON、证据、置信度和冲突状态；
5. 只有带原文段号的结果才会写入 aligned layers，低置信结果仍显示为待人工复核。

默认只处理华严经，也支持 --book dongpo_yizhuan。模型输出永远是候选，
不能直接据此提取古籍规则。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

import requests


BASE = Path("data/processed/canon")
TASKS = BASE / "alignment_tasks" / "huayan_and_dongpo.jsonl"
OUT = BASE / "alignment_candidates"
ALIGNED = BASE / "layers"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")


def read_tasks(book: str) -> list[dict]:
    return [
        json.loads(line)
        for line in TASKS.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("book") == book
    ]


def prompt_for(task: dict, extra: str = "") -> str:
    originals = "\n".join(
        f"O{row['segment_index']}: {row['text']}"
        for row in task["original_segments"]
    )
    modern = "\n".join(
        f"M{row['paragraph_index']}: {row['text']}"
        for row in task["modern_paragraphs"]
    )
    first_original = task["original_segments"][0]
    valid_indices = ",".join(str(row["segment_index"]) for row in task["original_segments"])
    expected_count = len(task["modern_paragraphs"])
    kind = "《大方广佛华严经》现代白话" if task["book"] == "huayan_t0279" else "《东坡易传》现代解读"
    return f"""你是古籍逐段对读校勘员。现在对齐{kind}。

目标是尽可能做到字句级对应，不是按字数比例分组，也不是概括全文。请比较原文 O 和现代段 M：

1. 必须为每一个 M 输出一个 mapping；现代段可以对应一个或多个连续 O 段，多个 M 也可以对应同一个 O 段。
2. 只允许使用给出的 O 段号；保持原文顺序单调，不得跳跃、倒序或编造段号。
3. 依据专名、佛典术语、句法结构、叙事顺序和明确释义判断。不能只看长度。
4. 现代译文若合并、拆分、增释或遗漏，relation 分别写 merge、split、interpretation、omission；无法确认时 original_segment_indices 写空数组。
5. evidence 只摘不超过 18 个字的原文短语，不要写长引文。
6. relation 只能取 translation、merge、split、interpretation、omission、uncertain 之一；confidence 只能取 high、medium、low 之一。
7. 必须返回 {expected_count} 个 mapping 对象，modern_paragraph_index 必须覆盖本窗口的全部 M 段。
8. 合法原文段号只有：{valid_indices}。只保留一个短 evidence 和一句极短 note，避免输出冗长解释。
9. 返回对象的字段是 modern_paragraph_index、original_segment_indices、relation、confidence、evidence、note，外层键是 mapping。

{extra}

原文段落：
{originals}

现代段落：
{modern}
"""


def parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON 对象")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("mapping"), list):
        raise ValueError("JSON 缺少 mapping 数组")
    return value


def normalize_mapping(value: dict, task: dict) -> tuple[dict[int, dict], list[str]]:
    allowed = {row["segment_index"] for row in task["original_segments"]}
    expected = {row["paragraph_index"] for row in task["modern_paragraphs"]}
    result: dict[int, dict] = {}
    errors: list[str] = []

    def numeric(value: Any) -> int:
        if isinstance(value, int):
            return value
        match = re.fullmatch(r"[MO]?(\d+)", str(value).strip())
        if not match:
            raise ValueError(value)
        return int(match.group(1))

    for raw in value.get("mapping", []):
        try:
            modern_index = numeric(raw["modern_paragraph_index"])
        except (KeyError, TypeError, ValueError):
            errors.append("缺少有效 modern_paragraph_index")
            continue
        if modern_index not in expected:
            errors.append(f"越界现代段 M{modern_index}")
            continue
        indices = raw.get("original_segment_indices", [])
        if not isinstance(indices, list):
            errors.append(f"M{modern_index} 的原文段号不是数组")
            indices = []
        cleaned: list[int] = []
        for index in indices:
            try:
                index = numeric(index)
            except (TypeError, ValueError):
                continue
            if index in allowed and index not in cleaned:
                cleaned.append(index)
            else:
                errors.append(f"M{modern_index} 含无效 O{index}")
        cleaned.sort()
        relation = str(raw.get("relation", "uncertain"))
        if relation not in {"translation", "merge", "split", "interpretation", "omission", "uncertain"}:
            relation = "uncertain"
        confidence = str(raw.get("confidence", "low"))
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        result[modern_index] = {
            "modern_paragraph_index": modern_index,
            "original_segment_indices": cleaned,
            "relation": relation,
            "confidence": confidence,
            "evidence": raw.get("evidence", []) if isinstance(raw.get("evidence", []), list) else [],
            "note": str(raw.get("note", ""))[:120],
        }
    for index in sorted(expected):
        result.setdefault(index, {
            "modern_paragraph_index": index,
            "original_segment_indices": [],
            "relation": "uncertain",
            "confidence": "low",
            "evidence": [],
            "note": "模型未返回该现代段",
        })
    previous: list[int] = []
    for index in sorted(result):
        current = result[index]["original_segment_indices"]
        if current and previous and min(current) < max(previous):
            errors.append(f"M{index} 与前段出现倒序")
        if current:
            previous = current
    return result, errors


def call_ollama(model: str, prompt: str, timeout: int = 1200) -> dict:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 7000},
            "keep_alive": 0,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    content = body.get("message", {}).get("content", "")
    return {"model": model, "raw": content, "response": parse_json_object(content)}


def call_gemini(model: str, prompt: str, timeout: int = 1200) -> dict:
    model_name = model.removeprefix("models/")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
        params={"key": os.environ["GEMINI_API_KEY"]},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        },
        timeout=min(timeout, 180),
    )
    response.raise_for_status()
    body = response.json()
    content = body["candidates"][0]["content"]["parts"][0]["text"]
    return {"model": model, "raw": content, "response": parse_json_object(content)}


def run_model(model: str, task: dict, extra: str = "") -> dict:
    started = time.time()
    try:
        if model.startswith("gemini"):
            result = call_gemini(model, prompt_for(task, extra))
        else:
            result = call_ollama(model, prompt_for(task, extra))
        mapping, errors = normalize_mapping(result["response"], task)
        result.update({"mapping": mapping, "errors": errors, "seconds": round(time.time() - started, 1), "ok": True})
        return result
    except Exception as exc:  # 保留失败信息，批任务不中断
        return {"model": model, "ok": False, "error": repr(exc), "seconds": round(time.time() - started, 1)}


def windowed_run(model: str, task: dict, window_size: int = 80) -> dict:
    """按现代段窗口调用模型，避免 300 段以上的 JSON 超出生成上限。"""
    paragraphs = task["modern_paragraphs"]
    windows = [paragraphs[start : start + window_size] for start in range(0, len(paragraphs), window_size)]
    merged: dict[int, dict] = {}
    runs: list[dict] = []
    errors: list[str] = []
    started = time.time()
    for number, selected in enumerate(windows):
        subtask = dict(task)
        subtask["modern_paragraphs"] = selected
        run = run_model(model, subtask, extra=(
            f"这是第 {number + 1}/{len(windows)} 个现代段窗口；只返回窗口内的 M 段。"
        ))
        runs.append(run)
        if not run.get("ok"):
            errors.append(f"窗口 {number + 1}: {run.get('error')}")
            continue
        for index, item in run["mapping"].items():
            if index in merged:
                old = merged[index]
                if old["original_segment_indices"] != item["original_segment_indices"]:
                    errors.append(f"M{index} 在重叠窗口中出现不同映射")
                if not old["original_segment_indices"] and item["original_segment_indices"]:
                    merged[index] = item
            else:
                merged[index] = item
        errors.extend(run.get("errors", []))
    expected = {row["paragraph_index"] for row in paragraphs}
    for index in expected - set(merged):
        merged[index] = {
            "modern_paragraph_index": index,
            "original_segment_indices": [],
            "relation": "uncertain",
            "confidence": "low",
            "evidence": [],
            "note": "窗口模型未返回",
        }
    merged, semantic_repairs = repair_by_semantic_anchors(task, merged)
    errors.extend(f"语义锚点修正: {repair}" for repair in semantic_repairs)
    return {
        "model": model,
        "ok": bool(merged),
        "mapping": dict(sorted(merged.items())),
        "windows": len(windows),
        "window_runs": runs,
        "errors": errors,
        "semantic_repairs": semantic_repairs,
        "seconds": round(time.time() - started, 1),
    }


def mapping_signature(mapping: dict[int, dict]) -> dict[int, tuple[int, ...]]:
    return {index: tuple(row["original_segment_indices"]) for index, row in mapping.items()}


def semantic_anchor_score(modern: str, original: str) -> float:
    """用中文字符和双字词重合度检查语义锚点，不单独作为校勘结论。"""
    clean_modern = re.sub(r"[^\u3400-\u9fff]", "", modern or "")
    clean_original = re.sub(r"[^\u3400-\u9fff]", "", original or "")
    if not clean_modern or not clean_original:
        return 0.0
    modern_chars = set(clean_modern)
    original_chars = set(clean_original)
    modern_bigrams = {clean_modern[i : i + 2] for i in range(len(clean_modern) - 1)}
    original_bigrams = {clean_original[i : i + 2] for i in range(len(clean_original) - 1)}
    char_score = len(modern_chars & original_chars) / max(
        1.0, math.sqrt(len(modern_chars) * len(original_chars))
    )
    bigram_score = len(modern_bigrams & original_bigrams) / max(
        1.0, math.sqrt(len(modern_bigrams) * len(original_bigrams))
    )
    return 0.35 * char_score + 0.65 * bigram_score


def repair_by_semantic_anchors(task: dict, mapping: dict[int, dict]) -> tuple[dict[int, dict], list[str]]:
    """修正模型把白话拆段错误推进到下一个原文段的情况。"""
    originals = task["original_segments"]
    moderns = {row["paragraph_index"]: row for row in task["modern_paragraphs"]}
    position = {row["segment_index"]: index for index, row in enumerate(originals)}
    repaired = {index: dict(row) for index, row in mapping.items()}
    repairs: list[str] = []
    last_position = 0
    for modern_index in sorted(repaired):
        item = repaired[modern_index]
        targets = item.get("original_segment_indices", [])
        if modern_index not in moderns or len(targets) != 1 or targets[0] not in position:
            if targets:
                last_position = max(last_position, max(position.get(target, last_position) for target in targets))
            continue
        predicted = position[targets[0]]
        # 必须包含上一个已确认段本身；白话可能连续多段都翻译同一个长原文段。
        lower = last_position
        upper = min(len(originals) - 1, predicted + 4)
        candidates = list(range(lower, upper + 1))
        scores = [
            semantic_anchor_score(moderns[modern_index]["text"], originals[index]["text"])
            for index in candidates
        ]
        best_index = candidates[max(range(len(candidates)), key=lambda index: scores[index])]
        predicted_score = semantic_anchor_score(moderns[modern_index]["text"], originals[predicted]["text"])
        best_score = semantic_anchor_score(moderns[modern_index]["text"], originals[best_index]["text"])
        if best_index != predicted and best_score >= 0.06 and best_score >= predicted_score + 0.025:
            old_target = targets[0]
            new_target = originals[best_index]["segment_index"]
            item["original_segment_indices"] = [new_target]
            item["original_segment_index"] = new_target
            item["confidence"] = "medium"
            item["note"] = f"语义锚点修正 O{old_target}→O{new_target}"
            repairs.append(f"M{modern_index}: O{old_target}->O{new_target} ({predicted_score:.3f}->{best_score:.3f})")
            predicted = best_index
        last_position = max(last_position, predicted)
    return repaired, repairs


def choose_result(task: dict, runs: list[dict]) -> tuple[dict[int, dict], str, str]:
    good = [run for run in runs if run.get("ok")]
    if not good:
        empty = {row["paragraph_index"]: {"modern_paragraph_index": row["paragraph_index"], "original_segment_indices": [], "relation": "uncertain", "confidence": "low", "evidence": [], "note": "模型调用失败"} for row in task["modern_paragraphs"]}
        return empty, "待重试：模型调用失败", "low"
    expected = len(task["modern_paragraphs"])
    coverage = [
        sum(bool(row["original_segment_indices"]) for row in run["mapping"].values()) / max(1, expected)
        for run in good
    ]
    signatures = [mapping_signature(run["mapping"]) for run in good]
    if len(good) == 1:
        if coverage[0] < 0.85:
            return good[0]["mapping"], "待重新提示：单模型覆盖不足", "low"
        return good[0]["mapping"], "待交叉复核：单模型候选", "low"
    if len(signatures) >= 2 and signatures[0] == signatures[1] and min(coverage) >= 0.85:
        return good[0]["mapping"], "已双模型一致", "high"
    if len(good) >= 2 and min(coverage[:2]) >= 0.85:
        first, second = good[0]["mapping"], good[1]["mapping"]
        similar = True
        merged: dict[int, dict] = {}
        for index in sorted(first):
            left = set(first[index]["original_segment_indices"])
            right = set(second[index]["original_segment_indices"])
            if left and right and not (left <= right or right <= left):
                similar = False
                break
            combined = sorted(left | right)
            chosen = second[index] if len(right) > len(left) else first[index]
            merged[index] = dict(chosen)
            merged[index]["original_segment_indices"] = combined
            merged[index]["confidence"] = "medium"
        if similar:
            return merged, "待人工复核：双模型部分一致", "medium"
    # 发生冲突时先按“有效覆盖率”选择候选，不能让一个输出了大量越界段号的
    # 模型因为排在最后就覆盖质量更高的结果；最终仍标记人工复核。
    best_index = max(range(len(good)), key=lambda index: coverage[index])
    chosen = good[best_index]
    if len(good) >= 3 and signatures[-1] == signatures[-2] and min(coverage[-2:]) >= 0.85:
        return good[-1]["mapping"], "已三模型一致", "high"
    if max(coverage) < 0.85:
        return chosen["mapping"], "待重新提示：模型覆盖不足", "low"
    return chosen["mapping"], "待人工复核：模型冲突", "low"


def save_candidate(task: dict, runs: list[dict], mapping: dict[int, dict], status: str, confidence: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task["task_id"],
        "book": task["book"],
        "volume": task.get("volume"),
        "chapter": task.get("chapter"),
        "chapter_title": task.get("chapter_title"),
        "status": status,
        "confidence": confidence,
        "mapping": sorted(
            mapping.values(),
            key=lambda item: int(item["modern_paragraph_index"]),
        ),
        "model_runs": [
            {key: value for key, value in run.items()}
            for run in runs
        ],
    }
    path = OUT / f"{task['task_id'].replace(':', '_')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def materialize(book: str) -> dict:
    tasks = {task["task_id"]: task for task in read_tasks(book)}
    records: list[dict] = []
    for path in sorted(OUT.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("book") != book or payload.get("task_id") not in tasks:
            continue
        task = tasks[payload["task_id"]]
        by_index = {row["paragraph_index"]: row for row in task["modern_paragraphs"]}
        for item in sorted(
            payload.get("mapping", []),
            key=lambda row: int(row["modern_paragraph_index"]),
        ):
            modern_index = item["modern_paragraph_index"]
            source = by_index.get(modern_index)
            targets = item.get("original_segment_indices", [])
            if source is None:
                continue
            target_text = ",".join(f"O{index}" for index in targets) if targets else "未定位"
            # 模型一致仍是 model_agree，不是 human_verified；禁止直接 high
            from numerology.corpus_quality import (
                REVIEW_CANDIDATE,
                REVIEW_MODEL_AGREE,
                STATUS_LABELS,
                build_provenance,
                confidence_for_review,
                normalize_review_status,
            )
            review = normalize_review_status(payload.get("status"), has_targets=bool(targets))
            if review == "human_verified":
                review = REVIEW_MODEL_AGREE  # materialize 路径永不写人工 verified
            if payload.get("confidence") == "high" and review == REVIEW_CANDIDATE:
                # 双模型一致状态文案可能被别名成 model_agree；否则保持 candidate
                if "一致" in str(payload.get("status", "")) and "部分" not in str(payload.get("status", "")):
                    review = REVIEW_MODEL_AGREE
            records.append({
                "book": book,
                "chapter": task["chapter"],
                "chapter_title": task.get("chapter_title"),
                "book_chapter_label": task.get("book_chapter_label"),
                "volume": task.get("volume"),
                "source_file": task.get("source_file"),
                "source_url": task.get("source_url"),
                "layer": "现代白话",
                "marker": f"M{modern_index} → {target_text}",
                "translation_source": "洪启嵩译" if book == "huayan_t0279" else "网站白话",
                "review_status": review,
                "confidence": confidence_for_review(review, item.get("confidence")),
                "text": source["text"],
                "segment_index": len(records),
                "source_paragraph_index": modern_index,
                "original_segment_indices": targets,
                "original_segment_index": targets[0] if targets else None,
                "relation": item.get("relation"),
                "evidence": item.get("evidence", []),
                "alignment_status": payload.get("status") or STATUS_LABELS[review],
                "alignment_method": "多模型逐段语义对齐；保留模型证据",
                "provenance": build_provenance(
                    pipeline="align_canon_models",
                    source="洪启嵩译" if book == "huayan_t0279" else "网站白话",
                    extra={
                        "task_id": payload.get("task_id"),
                        "model_status": payload.get("status"),
                        "payload_confidence": payload.get("confidence"),
                    },
                ),
            })
    output = ALIGNED / f"{book}_aligned_layers.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"output": str(output), "records": len(records), "tasks": len({r["chapter"] for r in records})}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default="huayan_t0279")
    parser.add_argument("--models", nargs="+", default=["qwen3:8b", "qwen3:30b-a3b"])
    parser.add_argument("--online-model", default="", help="仅在 --online-review 时作为第三模型")
    parser.add_argument("--online-review", action="store_true")
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--max-tasks", type=int, default=0)
    parser.add_argument("--materialize", action="store_true")
    args = parser.parse_args()
    tasks = read_tasks(args.book)
    if args.task_id:
        tasks = [task for task in tasks if task["task_id"] in set(args.task_id)]
    if args.max_tasks:
        tasks = tasks[: args.max_tasks]
    for number, task in enumerate(tasks, 1):
        runs = [windowed_run(model, task) for model in args.models]
        initial_mapping, initial_status, initial_confidence = choose_result(task, runs)
        if args.online_review and args.online_model and initial_status != "双模型一致":
            # 只把本地不一致/覆盖不足的任务交给第三模型，避免线上费用和延迟扩散到全量。
            runs.append(windowed_run(args.online_model, task))
        mapping, status, confidence = choose_result(task, runs)
        path = save_candidate(task, runs, mapping, status, confidence)
        print(f"[{number}/{len(tasks)}] {task['task_id']} {status} {confidence} -> {path}", flush=True)
    if args.materialize:
        print(json.dumps(materialize(args.book), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
