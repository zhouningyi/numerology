"""翻译/映射质量的统一状态、置信度与审计工具。

三条纪律：
1. 模型一致或生成成功 ≠ human_verified；
2. confidence=high 只允许在人工复核后写入；
3. 页面徽章与统计只读 review_status，不读模型自报 confidence。
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


# ── 统一状态词汇 ──────────────────────────────────────────────

REVIEW_CANDIDATE = "candidate"
REVIEW_MODEL_AGREE = "model_agree"
REVIEW_HUMAN_VERIFIED = "human_verified"
REVIEW_REJECTED = "rejected"

CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_HIGH = "high"

# 旧 alignment_status / 文案 → 统一 review_status
_STATUS_ALIASES = {
    "已对齐": REVIEW_CANDIDATE,
    "已双模型一致": REVIEW_MODEL_AGREE,
    "已三模型一致": REVIEW_MODEL_AGREE,
    "待交叉复核：单模型候选": REVIEW_CANDIDATE,
    "待人工复核：单模型候选（含语义锚点修正）": REVIEW_CANDIDATE,
    "待人工复核：双模型部分一致": REVIEW_CANDIDATE,
    "待人工复核：模型冲突": REVIEW_CANDIDATE,
    "待人工复核：原文小段独立翻译": REVIEW_CANDIDATE,
    "待高级模型语义对齐": REVIEW_CANDIDATE,
    "待语义对齐": REVIEW_CANDIDATE,
    "待重新提示：单模型覆盖不足": REVIEW_CANDIDATE,
    "待重新提示：模型覆盖不足": REVIEW_CANDIDATE,
    "待重试：模型调用失败": REVIEW_CANDIDATE,
    "按卦名聚合": REVIEW_CANDIDATE,  # 结构挂接，非句子级 verified
    "candidate": REVIEW_CANDIDATE,
    "model_agree": REVIEW_MODEL_AGREE,
    "human_verified": REVIEW_HUMAN_VERIFIED,
    "rejected": REVIEW_REJECTED,
    "verified": REVIEW_HUMAN_VERIFIED,
}

STATUS_LABELS = {
    REVIEW_CANDIDATE: "候选（待复核）",
    REVIEW_MODEL_AGREE: "模型一致（仍待人工）",
    REVIEW_HUMAN_VERIFIED: "人工已复核",
    REVIEW_REJECTED: "已驳回",
}

# 周易 section_key 规范化：去掉括号说明，统一传文名
_SECTION_PAREN_RE = re.compile(r"[（(].*?[）)]")
_SECTION_ALIASES = {
    "卦辞": "卦辞",
    "六爻": "六爻",
    "彖": "彖传",
    "彖传": "彖传",
    "象": "象传",
    "象传": "象传",
    "文言": "文言传",
    "文言传": "文言传",
    "用九": "用九",
    "用六": "用六",
}
_YAO_KEYS = {
    "初九", "九二", "九三", "九四", "九五", "上九",
    "初六", "六二", "六三", "六四", "六五", "上六",
    "用九", "用六",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_review_status(value: str | None, *, has_targets: bool = False) -> str:
    """把历史文案/状态归一到四个 review_status。"""
    if not value:
        return REVIEW_CANDIDATE if has_targets else REVIEW_CANDIDATE
    text = str(value).strip()
    if text in _STATUS_ALIASES:
        return _STATUS_ALIASES[text]
    if text.startswith("待"):
        return REVIEW_CANDIDATE
    if "一致" in text and "部分" not in text:
        return REVIEW_MODEL_AGREE
    if "verified" in text.lower() or "人工" in text and "通过" in text:
        return REVIEW_HUMAN_VERIFIED
    return REVIEW_CANDIDATE


def confidence_for_review(review_status: str, model_confidence: str | None = None) -> str:
    """对外展示/统计用置信度：人工 verified 才允许 high。"""
    if review_status == REVIEW_HUMAN_VERIFIED:
        return CONFIDENCE_HIGH
    if review_status == REVIEW_MODEL_AGREE:
        return CONFIDENCE_MEDIUM
    if review_status == REVIEW_REJECTED:
        return CONFIDENCE_LOW
    # 候选层：即便模型自报 high，也降为 low/medium 中的非 high
    if model_confidence == CONFIDENCE_MEDIUM:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def build_provenance(
    *,
    pipeline: str,
    model: str | None = None,
    prompt_version: str | None = None,
    source: str | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "pipeline": pipeline,
        "model": model,
        "prompt_version": prompt_version,
        "source": source,
        "written_at": utc_now_iso(),
    }
    if extra:
        payload.update(extra)
    return payload


def apply_quality_fields(
    row: dict,
    *,
    pipeline: str | None = None,
    force_candidate: bool = False,
) -> dict:
    """就地/返回补齐 review_status、confidence、provenance 字段。"""
    item = dict(row)
    has_targets = bool(
        item.get("original_segment_indices")
        or item.get("original_segment_index") is not None
    )
    missing_prov = missing_prov_if(item)
    explicit = item.get("review_status")
    if force_candidate:
        review = REVIEW_CANDIDATE
    elif explicit in {
        REVIEW_CANDIDATE, REVIEW_MODEL_AGREE, REVIEW_HUMAN_VERIFIED, REVIEW_REJECTED,
    }:
        review = explicit
    else:
        review = normalize_review_status(
            item.get("alignment_status") or item.get("status"),
            has_targets=has_targets,
        )
        # 缺 provenance 的“已对齐/high”一律降级：无法审计来源
        if missing_prov and item.get("layer") in {"现代白话", "现代释译"}:
            review = REVIEW_CANDIDATE

    item["review_status"] = review
    item["confidence"] = confidence_for_review(review, item.get("confidence"))
    # 缺 provenance 或强制候选时，用统一标签覆盖夸大的历史文案
    if force_candidate or missing_prov or not item.get("alignment_status"):
        item["alignment_status"] = STATUS_LABELS[review]
    if pipeline and not item.get("provenance"):
        item["provenance"] = build_provenance(
            pipeline=pipeline,
            model=item.get("model"),
            prompt_version=item.get("prompt_version"),
            source=item.get("translation_source"),
        )
    return item


def missing_prov_if(item: dict) -> bool:
    return not (
        item.get("provenance")
        or item.get("model")
        or item.get("prompt_version")
        or item.get("source_text")
    )


def normalize_section_key(key: str | None) -> str | None:
    """周易结构键规范化：文言传（节要）→ 文言传。"""
    if not key:
        return None
    text = _SECTION_PAREN_RE.sub("", str(key)).strip()
    text = re.sub(r"\s+", "", text)
    if text in _YAO_KEYS:
        return text
    if text in _SECTION_ALIASES:
        return _SECTION_ALIASES[text]
    # 以爻名开头的键
    for yao in _YAO_KEYS:
        if text.startswith(yao):
            return yao
    return text or None


def attach_normalized_section_keys(segments: Iterable[dict]) -> list[dict]:
    result = []
    for row in segments:
        item = dict(row)
        raw = item.get("section_key")
        item["section_key_raw"] = raw
        item["section_key"] = normalize_section_key(raw)
        result.append(item)
    return result


# ── 对齐挂接策略 ──────────────────────────────────────────────

def resolve_inline_alignment(
    original_segments: list[dict],
    inline_items: list[dict],
) -> dict[str, Any]:
    """原文中心挂接：段号优先 → 规范化 section_key → 禁止静默等长配对。

    返回:
      inline_by_original: list[list]
      unmatched_inline: list
      pending_unmapped: list
      method: str
    """
    inline_by_original: list[list] = [[] for _ in original_segments]
    unmatched: list[dict] = []
    pending_unmapped = [
        item for item in inline_items
        if str(item.get("alignment_status", "")).startswith("待")
        and not (
            item.get("original_segment_indices")
            or item.get("original_segment_index") is not None
        )
        and item.get("review_status") not in {REVIEW_HUMAN_VERIFIED, REVIEW_MODEL_AGREE}
    ]

    original_positions = {
        s.get("segment_index"): index
        for index, s in enumerate(original_segments)
        if s.get("segment_index") is not None
    }
    mapped = [
        s for s in inline_items
        if s.get("original_segment_indices") or s.get("original_segment_index") is not None
    ]

    method = "none"
    if mapped and original_positions:
        method = "original_segment_index"
        for item in mapped:
            targets = item.get("original_segment_indices") or [item.get("original_segment_index")]
            positions = [original_positions.get(index) for index in targets]
            positions = [p for p in positions if p is not None]
            if not positions or len(positions) != len([t for t in targets if t is not None]):
                unmatched.append(item)
            else:
                for position in positions:
                    inline_by_original[position].append(item)
        unmatched.extend(item for item in inline_items if item not in mapped)
    elif inline_items:
        # 结构键：允许多个译文挂同一键（如白话合并），原文按首次出现挂接
        keyed_original: dict[str, list[int]] = defaultdict(list)
        for index, seg in enumerate(original_segments):
            key = normalize_section_key(seg.get("section_key"))
            if key:
                keyed_original[key].append(index)
        keyed_items = []
        for item in inline_items:
            key = normalize_section_key(item.get("section_key"))
            if key:
                keyed_items.append((item, key))
        if keyed_items and len(keyed_items) == len(inline_items) and keyed_original:
            method = "section_key"
            used_slots: dict[str, int] = defaultdict(int)
            for item, key in keyed_items:
                slots = keyed_original.get(key) or []
                slot_i = used_slots[key]
                if slot_i < len(slots):
                    inline_by_original[slots[slot_i]].append(item)
                    used_slots[key] += 1
                elif slots:
                    # 额外白话挂到该键最后一个原文段（拆译）
                    inline_by_original[slots[-1]].append(item)
                else:
                    unmatched.append(item)
        else:
            # 故意不做“段数相等即顺序对齐”——那会把解释文伪装成逐段译文
            method = "unmatched_no_equal_length"
            unmatched = list(inline_items)

    if pending_unmapped:
        unmatched = [item for item in unmatched if item in pending_unmapped]

    return {
        "inline_by_original": inline_by_original,
        "unmatched_inline": unmatched,
        "pending_unmapped": pending_unmapped,
        "method": method,
    }


# ── 审计 ──────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def audit_nde(base: Path | None = None) -> dict:
    """濒死：现象分类、译文、概念证据、中文圈注覆盖。"""
    base = base or Path("data/processed/nderf")
    experiences = _read_jsonl(base / "experiences.jsonl")
    translations = _read_jsonl(base / "translations.jsonl")
    evidence_zh = _read_jsonl(base / "evidence_zh.jsonl")

    from numerology.nde.parser import classify, load_phenomena

    phenomena = load_phenomena()
    category_counts = Counter()
    for row in experiences:
        for key in row.get("categories") or {}:
            category_counts[key] += 1

    # 用现有 qa 重跑规则，检测规则变更 diff
    reclass_diff = {"added": 0, "removed": 0, "changed_cases": 0}
    sample_changes: list[dict] = []
    for row in experiences:
        qa = row.get("qa") or []
        if not qa:
            continue
        fresh = classify(qa, phenomena)
        old = set((row.get("categories") or {}).keys())
        new = set(fresh.keys())
        if old != new:
            reclass_diff["changed_cases"] += 1
            reclass_diff["added"] += len(new - old)
            reclass_diff["removed"] += len(old - new)
            if len(sample_changes) < 8:
                sample_changes.append({
                    "slug": row.get("slug"),
                    "added": sorted(new - old),
                    "removed": sorted(old - new),
                })

    tr_by_slug = {r["slug"]: r for r in translations if "slug" in r}
    ev_by_slug = {r["slug"]: r.get("concepts_zh") or {} for r in evidence_zh if "slug" in r}
    concepted = [r for r in translations if r.get("concepts")]
    incomplete_evidence = 0
    missing_evidence = 0
    for row in concepted:
        zh = ev_by_slug.get(row["slug"])
        if zh is None:
            missing_evidence += 1
            continue
        if len(zh) < len(row["concepts"]):
            incomplete_evidence += 1

    gift_yes_est = 0
    for row in experiences:
        for pair in row.get("qa") or []:
            q = (pair.get("q") or "").lower()
            a = (pair.get("a") or "").lower()
            if "special gift" in q or "psychic" in q:
                if a.startswith("yes") or a.startswith("i ") and "yes" in a[:40]:
                    gift_yes_est += 1
                break

    flags = []
    aftereffects = category_counts.get("aftereffects_gifts", 0)
    if aftereffects < max(50, gift_yes_est // 20):
        flags.append({
            "code": "nde_aftereffects_undercount",
            "severity": "error",
            "message": (
                f"aftereffects_gifts={aftereffects}，问卷肯定倾向约 {gift_yes_est}；"
                "规则可能过严或 experiences 未重分类"
            ),
        })
    if concepted and missing_evidence / len(concepted) > 0.2:
        flags.append({
            "code": "nde_evidence_zh_gap",
            "severity": "warning",
            "message": f"有概念但无中文圈注：{missing_evidence}/{len(concepted)}",
        })
    if incomplete_evidence:
        flags.append({
            "code": "nde_evidence_zh_incomplete",
            "severity": "warning",
            "message": f"中文圈注键少于概念数：{incomplete_evidence}",
        })

    return {
        "domain": "nde",
        "counts": {
            "experiences": len(experiences),
            "translations": len(translations),
            "concepted_translations": len(concepted),
            "evidence_zh": len(evidence_zh),
            "categories": dict(category_counts),
            "gift_yes_estimate": gift_yes_est,
        },
        "reclass_diff": {**reclass_diff, "samples": sample_changes},
        "coverage": {
            "translation_rate": round(len(tr_by_slug) / max(1, len(experiences)), 4),
            "evidence_zh_rate": round(
                (len(concepted) - missing_evidence) / max(1, len(concepted)), 4
            ),
        },
        "flags": flags,
    }


def audit_yijing(layers_dir: Path | None = None) -> dict:
    """周易：结构完整性、section_key join、东坡挂接。"""
    layers_dir = layers_dir or Path("data/processed/canon/layers")
    yijing = _read_jsonl(layers_dir / "yijing_layers.jsonl")
    related = _read_jsonl(layers_dir / "yijing_related_layers.jsonl")
    general_path = layers_dir / "yijing_related_sources.json"
    general = {}
    if general_path.exists():
        try:
            general = json.loads(general_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            general = {}

    by_chapter: dict[int, list[dict]] = defaultdict(list)
    for row in yijing:
        ch = row.get("chapter")
        if ch is not None:
            by_chapter[int(ch)].append(row)

    exact = partial = none = 0
    structure_gaps = []
    join_rows = []
    for chapter, segs in sorted(by_chapter.items()):
        if chapter > 64:
            continue
        originals = [s for s in segs if s.get("layer") == "原文"]
        moderns = [s for s in segs if s.get("layer") == "现代白话"]
        o_keys = [normalize_section_key(s.get("section_key")) for s in originals]
        m_keys = [normalize_section_key(s.get("section_key")) for s in moderns]
        o_set = {k for k in o_keys if k}
        m_set = {k for k in m_keys if k}
        if originals and moderns:
            if o_keys == m_keys:
                exact += 1
                status = "exact"
            elif o_set & m_set:
                partial += 1
                status = "partial"
            else:
                none += 1
                status = "none"
            yao_orig = [k for k in o_keys if k in _YAO_KEYS and k not in {"用九", "用六"}]
            yao_mod = [k for k in m_keys if k in _YAO_KEYS and k not in {"用九", "用六"}]
            join_rows.append({
                "chapter": chapter,
                "status": status,
                "original_keys": o_keys,
                "modern_keys": m_keys,
                "yao_join": len(set(yao_orig) & set(yao_mod)),
                "yao_original": len(set(yao_orig)),
                "yao_modern": len(set(yao_mod)),
            })
            if len(set(yao_orig)) < 6:
                structure_gaps.append({
                    "chapter": chapter,
                    "yao_count": len(set(yao_orig)),
                    "keys": o_keys,
                })

    unmatched_dongpo = general.get("unmatched_dongpo") or []
    flags = []
    if none:
        flags.append({
            "code": "yijing_section_key_none",
            "severity": "warning",
            "message": f"{none} 卦原文/白话结构键无交集",
        })
    if structure_gaps:
        flags.append({
            "code": "yijing_structure_gap",
            "severity": "warning",
            "message": f"{len(structure_gaps)} 卦爻题不足 6",
        })
    if unmatched_dongpo:
        flags.append({
            "code": "yijing_dongpo_unmatched",
            "severity": "info",
            "message": f"东坡未挂接 {len(unmatched_dongpo)} 条（含底本缺页）",
        })

    hexagram_chapters = [c for c in by_chapter if 1 <= c <= 64]
    return {
        "domain": "yijing",
        "counts": {
            "segments": len(yijing),
            "hexagrams": len(hexagram_chapters),
            "related_records": len(related),
            "join_exact": exact,
            "join_partial": partial,
            "join_none": none,
        },
        "structure_gaps": structure_gaps[:20],
        "join_sample": join_rows[:10],
        "unmatched_dongpo": unmatched_dongpo[:10],
        "flags": flags,
    }


def audit_huayan(layers_dir: Path | None = None) -> dict:
    """华严：各层覆盖、状态、provenance、非法 high。"""
    layers_dir = layers_dir or Path("data/processed/canon/layers")
    original = _read_jsonl(layers_dir / "huayan_t0279_layers.jsonl")
    modern = _read_jsonl(layers_dir / "huayan_t0279_modern_layers.jsonl")
    aligned = _read_jsonl(layers_dir / "huayan_t0279_aligned_layers.jsonl")
    generated = _read_jsonl(layers_dir / "huayan_t0279_generated_layers.jsonl")

    def layer_stats(rows: list[dict], name: str) -> dict:
        review = Counter()
        conf = Counter()
        missing_prov = 0
        illegal_high = 0
        mapped = 0
        for row in rows:
            normalized = apply_quality_fields(row)
            review[normalized["review_status"]] += 1
            conf[normalized["confidence"]] += 1
            if missing_prov_if(row):
                missing_prov += 1
            if (
                row.get("confidence") == CONFIDENCE_HIGH
                and normalize_review_status(row.get("review_status") or row.get("alignment_status"))
                != REVIEW_HUMAN_VERIFIED
            ):
                illegal_high += 1
            if row.get("original_segment_indices") or row.get("original_segment_index") is not None:
                mapped += 1
        return {
            "name": name,
            "count": len(rows),
            "review_status": dict(review),
            "confidence": dict(conf),
            "missing_provenance": missing_prov,
            "illegal_high": illegal_high,
            "mapped_to_original": mapped,
        }

    gen_targets = set()
    for row in generated:
        if row.get("original_segment_indices"):
            gen_targets.update(row["original_segment_indices"])
        elif row.get("original_segment_index") is not None:
            gen_targets.add(row["original_segment_index"])
        elif row.get("segment_index") is not None:
            gen_targets.add(row["segment_index"])
    orig_ids = {row.get("segment_index") for row in original}
    missing_gen = sorted(i for i in orig_ids if i not in gen_targets and i is not None)

    stats = {
        "original": layer_stats(original, "原文"),
        "modern_web": layer_stats(modern, "网页白话"),
        "aligned": layer_stats(aligned, "模型对齐白话"),
        "generated": layer_stats(generated, "项目自译/生成"),
    }
    flags = []
    if stats["generated"]["illegal_high"]:
        flags.append({
            "code": "huayan_generated_illegal_high",
            "severity": "error",
            "message": (
                f"generated 层有 {stats['generated']['illegal_high']} 条 "
                "confidence=high 但非 human_verified"
            ),
        })
    if stats["generated"]["missing_provenance"]:
        flags.append({
            "code": "huayan_generated_missing_provenance",
            "severity": "warning",
            "message": f"generated 缺 provenance：{stats['generated']['missing_provenance']}",
        })
    if missing_gen:
        flags.append({
            "code": "huayan_generated_coverage_gap",
            "severity": "info",
            "message": f"原文未覆盖自译 {len(missing_gen)}/{len(orig_ids)} 段",
        })
    if stats["aligned"]["count"] < 100 and stats["modern_web"]["count"]:
        flags.append({
            "code": "huayan_aligned_sparse",
            "severity": "info",
            "message": (
                f"逐段对齐仅 {stats['aligned']['count']} 条，"
                f"网页白话回退 {stats['modern_web']['count']} 品/卷"
            ),
        })

    return {
        "domain": "huayan",
        "layers": stats,
        "generated_coverage": {
            "original_segments": len(orig_ids),
            "covered": len(gen_targets & orig_ids),
            "missing": len(missing_gen),
            "missing_sample": missing_gen[:15],
        },
        "flags": flags,
    }


def run_all_audits(
    *,
    nde_dir: Path | None = None,
    layers_dir: Path | None = None,
) -> dict:
    report = {
        "checked_at": utc_now_iso(),
        "rule_version": "corpus-mapping-v1",
        "domains": {
            "nde": audit_nde(nde_dir),
            "yijing": audit_yijing(layers_dir),
            "huayan": audit_huayan(layers_dir),
        },
    }
    all_flags = []
    for domain, body in report["domains"].items():
        for flag in body.get("flags") or []:
            all_flags.append({"domain": domain, **flag})
    report["flag_count"] = len(all_flags)
    report["flags"] = all_flags
    report["severity_counts"] = dict(Counter(f["severity"] for f in all_flags))
    return report


def normalize_generated_huayan_rows(rows: list[dict]) -> list[dict]:
    """把华严 generated 层降到可审计候选态。"""
    output = []
    for row in rows:
        item = dict(row)
        missing = missing_prov_if(item)
        if missing:
            item["review_status"] = REVIEW_CANDIDATE
            item["confidence"] = CONFIDENCE_LOW
            item["alignment_status"] = "待人工复核：缺 provenance 的历史生成层"
            item["alignment_method"] = item.get("alignment_method") or "历史生成层，来源不可审计"
            item["provenance"] = build_provenance(
                pipeline="normalize_corpus_layers.generated_legacy",
                source=item.get("translation_source"),
                extra={"legacy": True, "note": "原记录无 model/prompt_version/source_text"},
            )
        else:
            # 有 provenance 的自译仍默认 candidate，除非已 human_verified
            if item.get("review_status") != REVIEW_HUMAN_VERIFIED:
                item["review_status"] = REVIEW_CANDIDATE
                item["confidence"] = CONFIDENCE_LOW
                item["alignment_status"] = item.get("alignment_status") or STATUS_LABELS[REVIEW_CANDIDATE]
            item = apply_quality_fields(item, pipeline="translate_huayan_segments")
        # 统一挂接字段
        if item.get("original_segment_index") is None and item.get("segment_index") is not None:
            if not item.get("original_segment_indices"):
                item["original_segment_index"] = item["segment_index"]
                item["original_segment_indices"] = [item["segment_index"]]
        output.append(item)
    return output
