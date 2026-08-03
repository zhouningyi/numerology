#!/usr/bin/env python3
"""Web server for browsing numerology data."""

import math
import difflib
import json
import os
import re
import sqlite3
from pathlib import Path

import markdown
import yaml
from datetime import datetime, timezone
from flask import (
    Flask, abort, redirect, render_template, request, jsonify, send_file,
    send_from_directory,
)

from process_canon_layers import BOOKS as CANON_BOOKS
from numerology.corpus_quality import (
    STATUS_LABELS as CORPUS_STATUS_LABELS,
    apply_quality_fields,
    resolve_inline_alignment,
)
from numerology.nde.parser import load_phenomena
from numerology.nde.search import EmbeddingIndex, MATRIX_PATH as NDE_MATRIX_PATH
from translate_nderf import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder="templates")
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "numerology.db"
DOCS_DIR = BASE_DIR / "docs"
CANON_PROCESSED_DIR = BASE_DIR / "data" / "processed" / "canon"
CANON_LAYERS_DIR = CANON_PROCESSED_DIR / "layers"
CANON_OCR_DIR = CANON_PROCESSED_DIR / "ocr"
CANON_SCAN_IMAGES_DIR = CANON_PROCESSED_DIR / "scans"
CANON_SCAN_DIR = BASE_DIR / "data" / "raw" / "canon" / "wikimedia"
HUAYAN_SCAN_DIR = BASE_DIR / "data" / "raw" / "canon" / "huayan_t0279" / "scans"

LAYER_LABELS = {
    "原文": "原文（原著正文）",
    "原注": "原注（刘基）",
    "评注": "评注（徐注/任氏曰/眉批）",
    "现代白话": "白话译文",
    "现代释译": "现代释译（项目整理）",
    "相关著作": "相关著作（跨书聚合）",
    "站点内容": "站点内容（不入统计）",
}
LAYER_BADGES = {
    "原文": "badge-water", "原注": "badge-earth", "评注": "badge-yin",
    "现代白话": "badge-wood", "现代释译": "badge-wood", "相关著作": "badge-dd", "站点内容": "badge-dd",
}

QUALITY_LABELS = {
    "blank_name": "姓名为空",
    "non_person_entry": "非人物条目",
    "future_birth_year": "出生年份晚于当前年份",
    "death_before_birth": "死亡早于出生",
    "lifespan_over_120": "推导寿命超过 120 年",
    "latitude_out_of_range": "纬度越界",
    "longitude_out_of_range": "经度越界",
    "birth_time_invalid": "出生时分无效",
    "adb_birth_year_out_of_scope": "ADB 出生年份超出范围",
    "adb_coordinate_out_of_range": "ADB 经纬度越界",
    "adb_missing_birth_time": "ADB 缺少出生时刻",
    "adb_unknown_rodden_rating": "ADB Rodden 评级未知",
    "cbdb_extreme_year": "CBDB 年份异常",
    "cbdb_unknown_name": "CBDB 人名未详",
    "partial_event_date": "事件日期精度不足",
    "malformed_event_date": "事件日期格式错误",
    "invalid_bazi_pillar": "八字柱格式错误",
    "bazi_without_time": "四柱标记与出生时刻不一致",
    "bazi_for_non_adb": "非 ADB 来源产生八字",
    "orphan_source_record": "来源记录未映射人物",
    "invalid_fact_interval": "出生事实区间无效",
}

ENTITY_LABELS = {
    "person": "人物",
    "event": "事件",
    "source_record": "来源记录",
    "birth_fact": "出生事实",
}

SEVERITY_LABELS = {"error": "错误", "warning": "警告", "info": "提示"}

SOURCE_LABELS = {
    "adb": "Astro-Databank（ADB）",
    "cbdb": "中国历代人物传记资料库（CBDB）",
    "wikidata": "Wikidata",
}
SOURCE_SHORT_LABELS = {"adb": "ADB", "cbdb": "CBDB", "wikidata": "Wikidata"}
FACT_TYPE_LABELS = {
    "event": "事件",
    "category": "分类",
    "biography": "传记事实",
    "relation": "人物关系",
}
FACT_SOURCE_LABELS = {
    "events_normalized": "标准化事件",
    "categories": "来源分类",
    "biography": "传记原文",
}
ANALYSIS_TIER_LABELS = {
    "full_bazi": "四柱：有日期和时刻",
    "three_pillars": "三柱：无出生时刻",
    "date_interval": "日期区间：精度不足",
    "unusable": "不可用于出生分析",
}
OUTCOME_STATUS_LABELS = {"positive": "已观察到", "unknown": "未知", "censored": "删失"}
TARGET_TYPE_LABELS = {
    "binary": "二元结果", "time_to_event": "事件时间", "count": "次数", "recurrent": "重复事件",
}

DETAIL_LABELS = {
    "source_id": "来源 ID", "name": "姓名", "entry_type": "条目类型",
    "birth_year": "出生年", "death_year": "死亡年", "lifespan_years": "推导寿命",
    "birth_lat": "纬度", "birth_lon": "经度", "birth_lat_raw": "原始纬度",
    "birth_lon_raw": "原始经度", "birth_time": "出生时刻", "birth_hour": "出生小时",
    "birth_minute": "出生分钟", "time_unknown": "时间未知标记", "time_accuracy": "时间精度",
    "rodden_rating": "Rodden 评级", "event_date": "事件日期", "event_code": "事件类型",
    "person_id": "人物 ID", "snapshot_id": "快照 ID", "raw_key": "原始键",
    "source_record_id": "来源记录 ID", "date_start": "区间开始", "date_end": "区间结束",
    "raw_year": "原始年", "raw_month": "原始月", "raw_day": "原始日",
    "has_time_pillar": "含时柱", "year_pillar": "年柱", "month_pillar": "月柱",
    "day_pillar": "日柱", "time_pillar": "时柱",
}


def localize_details(details_json: str | None) -> str:
    """把审计详情中的内部字段名转换为页面可读中文。"""
    if not details_json:
        return "—"
    try:
        details = json.loads(details_json)
    except json.JSONDecodeError:
        return details_json
    return "；".join(
        f"{DETAIL_LABELS.get(key, key)}：{value}" for key, value in details.items()
    )


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── 首页：数据概览 ──────────────────────────────────────────────
@app.route("/")
def index():
    db = get_db()
    stats = {}

    stats["total"] = db.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    stats["adb"] = db.execute(
        "SELECT COUNT(*) FROM persons WHERE source='adb'"
    ).fetchone()[0]
    stats["wikidata"] = db.execute(
        "SELECT COUNT(*) FROM persons WHERE source='wikidata'"
    ).fetchone()[0]
    stats["cbdb"] = db.execute(
        "SELECT COUNT(*) FROM persons WHERE source='cbdb'"
    ).fetchone()[0]
    stats["with_time"] = db.execute(
        "SELECT COUNT(*) FROM bazi WHERE has_time_pillar=1"
    ).fetchone()[0]

    quality_run = db.execute(
        "SELECT * FROM quality_audit_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    quality_counts = db.execute(
        """SELECT severity, COUNT(*) AS cnt
           FROM data_quality_flags
           WHERE audit_run_id = ?
           GROUP BY severity""",
        (quality_run["id"] if quality_run else -1,),
    ).fetchall()

    # 日主五行分布
    element_dist = db.execute("""
        SELECT day_master_element as elem, COUNT(*) as cnt
        FROM bazi GROUP BY day_master_element ORDER BY cnt DESC
    """).fetchall()

    # 日主天干分布
    stem_dist = db.execute("""
        SELECT day_master as stem, COUNT(*) as cnt
        FROM bazi GROUP BY day_master ORDER BY cnt DESC
    """).fetchall()

    # Rodden 评级分布
    rodden_dist = db.execute("""
        SELECT rodden_rating as rating, COUNT(*) as cnt
        FROM persons WHERE source='adb' AND rodden_rating IS NOT NULL
        GROUP BY rodden_rating ORDER BY cnt DESC
    """).fetchall()

    # 性别分布
    gender_dist = db.execute("""
        SELECT COALESCE(gender, 'Unknown') as g, COUNT(*) as cnt
        FROM persons GROUP BY g ORDER BY cnt DESC
    """).fetchall()

    db.close()
    return render_template(
        "index.html",
        stats=stats,
        element_dist=element_dist,
        stem_dist=stem_dist,
        rodden_dist=rodden_dist,
        gender_dist=gender_dist,
        quality_run=quality_run,
        quality_counts=quality_counts,
        quality_labels=QUALITY_LABELS,
        severity_labels=SEVERITY_LABELS,
        source_labels=SOURCE_LABELS,
        source_short_labels=SOURCE_SHORT_LABELS,
    )


# ── 人物列表 ────────────────────────────────────────────────────
@app.route("/persons")
def persons_list():
    db = get_db()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    search = request.args.get("q", "").strip()
    element = request.args.get("element", "")
    rodden = request.args.get("rodden", "")
    gender = request.args.get("gender", "")
    source = request.args.get("source", "")
    analysis_tier = request.args.get("analysis_tier", "")
    entry_type = request.args.get("entry_type", "person")  # 默认只看人物

    where = ["1=1"]
    params = []

    if entry_type:
        where.append("p.entry_type = ?")
        params.append(entry_type)
    if search:
        where.append("p.name LIKE ?")
        params.append(f"%{search}%")
    if element:
        where.append("b.day_master_element = ?")
        params.append(element)
    if rodden:
        where.append("p.rodden_rating = ?")
        params.append(rodden)
    if gender:
        where.append("p.gender = ?")
        params.append(gender)
    if source:
        where.append("p.source = ?")
        params.append(source)
    if analysis_tier:
        where.append("q.analysis_tier = ?")
        params.append(analysis_tier)

    where_sql = " AND ".join(where)

    total = db.execute(
        f"""SELECT COUNT(*) FROM persons p
               LEFT JOIN bazi b ON b.person_id=p.id
               LEFT JOIN person_quality_profiles q ON q.person_id=p.id
               WHERE {where_sql}""",
        params,
    ).fetchone()[0]

    rows = db.execute(
        f"""SELECT p.id, p.name, p.first_name, p.last_name, p.gender,
                   p.source,
                   p.birth_date, p.birth_time,
                   p.rodden_rating, p.birth_country,
                   b.year_pillar, b.month_pillar, b.day_pillar, b.time_pillar,
                   b.day_master, b.day_master_element, b.day_master_yinyang,
                   b.has_time_pillar, q.analysis_tier
            FROM persons p
            LEFT JOIN bazi b ON b.person_id = p.id
            LEFT JOIN person_quality_profiles q ON q.person_id = p.id
            WHERE {where_sql}
            ORDER BY p.name
            LIMIT ? OFFSET ?""",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()

    total_pages = math.ceil(total / per_page) if total > 0 else 1

    db.close()
    return render_template(
        "persons.html",
        persons=rows,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        search=search,
        element=element,
        rodden=rodden,
        gender=gender,
        source=source,
        analysis_tier=analysis_tier,
        entry_type=entry_type,
        source_labels=SOURCE_LABELS,
        source_short_labels=SOURCE_SHORT_LABELS,
        analysis_tier_labels=ANALYSIS_TIER_LABELS,
    )


# ── 人物详情 ────────────────────────────────────────────────────
@app.route("/person/<int:person_id>")
def person_detail(person_id):
    db = get_db()

    person = db.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
    if not person:
        return "Not found", 404

    bazi = db.execute("SELECT * FROM bazi WHERE person_id = ?", (person_id,)).fetchone()

    dayun = db.execute(
        "SELECT * FROM dayun WHERE person_id = ? ORDER BY start_age",
        (person_id,),
    ).fetchall()

    categories = db.execute(
        "SELECT * FROM categories WHERE person_id = ? ORDER BY cat_type, category",
        (person_id,),
    ).fetchall()

    events = db.execute(
        "SELECT * FROM events WHERE person_id = ? ORDER BY event_date",
        (person_id,),
    ).fetchall()

    life_facts = db.execute(
        """SELECT id, fact_type, fact_subtype, date_start, date_end,
                      date_precision, value_text, place, review_status,
                      source_table, source_id
               FROM biography_facts
               WHERE person_id = ?
               ORDER BY CASE WHEN date_start IS NULL THEN 1 ELSE 0 END,
                        date_start, id
               LIMIT 100""",
        (person_id,),
    ).fetchall()

    quality_profile = db.execute(
        "SELECT * FROM person_quality_profiles WHERE person_id = ?",
        (person_id,),
    ).fetchone()

    birth_facts = db.execute(
        """SELECT calendar, date_start, date_end, date_precision,
                      raw_year, raw_month, raw_day, raw_range_code
               FROM birth_facts WHERE person_id = ? ORDER BY id""",
        (person_id,),
    ).fetchall()
    death_facts = db.execute(
        """SELECT calendar, date_start, date_end, date_precision,
                      raw_year, raw_month, raw_day, raw_range_code, death_age
               FROM death_facts WHERE person_id = ? ORDER BY id""",
        (person_id,),
    ).fetchall()

    quality_run = db.execute(
        "SELECT * FROM quality_audit_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    quality_flags = db.execute(
        """SELECT f.*, r.rule_version, r.checked_at, e.event_date, e.event_code
           FROM data_quality_flags f
           JOIN quality_audit_runs r ON r.id = f.audit_run_id
           LEFT JOIN events e ON f.entity_type = 'event' AND e.id = f.entity_id
           WHERE r.id = ?
             AND ((f.entity_type = 'person' AND f.entity_id = ?)
               OR (f.entity_type = 'event' AND e.person_id = ?))
           ORDER BY CASE f.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                    f.flag_code""",
        (quality_run["id"] if quality_run else -1, person_id, person_id),
    ).fetchall()

    quality_flags = [
        {**dict(row), "details_text": localize_details(row["details_json"])}
        for row in quality_flags
    ]
    prediction_outcomes = db.execute(
        """SELECT o.*, d.name AS domain_name, d.target_type, d.absence_policy
           FROM person_prediction_outcomes o
           JOIN prediction_domains d ON d.code = o.domain_code
           WHERE o.person_id = ? ORDER BY d.code""",
        (person_id,),
    ).fetchall()
    db.close()
    return render_template(
        "person_detail.html",
        person=person,
        bazi=bazi,
        dayun=dayun,
        categories=categories,
        events=events,
        life_facts=life_facts,
        fact_type_labels=FACT_TYPE_LABELS,
        fact_source_labels=FACT_SOURCE_LABELS,
        quality_profile=quality_profile,
        analysis_tier_labels=ANALYSIS_TIER_LABELS,
        birth_facts=birth_facts,
        death_facts=death_facts,
        quality_flags=quality_flags,
        quality_run=quality_run,
        quality_labels=QUALITY_LABELS,
        entity_labels=ENTITY_LABELS,
        severity_labels=SEVERITY_LABELS,
        source_labels=SOURCE_LABELS,
        prediction_outcomes=prediction_outcomes,
        outcome_status_labels=OUTCOME_STATUS_LABELS,
        target_type_labels=TARGET_TYPE_LABELS,
    )


def load_corpus_mapping_report() -> dict | None:
    """读取濒死/周易/华严映射审计 latest 报告。"""
    path = BASE_DIR / "data" / "audits" / "corpus_mapping_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── 数据质量审计 ────────────────────────────────────────────────
@app.route("/quality")
def quality_dashboard():
    """显示最近一次逐条质量审计结果。"""
    db = get_db()
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 50, type=int), 10), 100)
    severity = request.args.get("severity", "")
    flag_code = request.args.get("flag_code", "")
    entity_type = request.args.get("entity_type", "")
    corpus_report = load_corpus_mapping_report()

    runs = db.execute(
        "SELECT * FROM quality_audit_runs ORDER BY id DESC LIMIT 20"
    ).fetchall()
    requested_run = request.args.get("run_id", type=int)
    if requested_run:
        run = db.execute(
            "SELECT * FROM quality_audit_runs WHERE id = ?", (requested_run,)
        ).fetchone()
    else:
        run = runs[0] if runs else None

    if not run:
        db.close()
        return render_template(
            "quality.html", run=None, runs=[], counts=[], flags=[], total=0,
            page=1, total_pages=1, severity=severity, flag_code=flag_code,
            entity_type=entity_type, flag_codes=[],
            quality_labels=QUALITY_LABELS, entity_labels=ENTITY_LABELS,
            severity_labels=SEVERITY_LABELS,
            corpus_report=corpus_report,
            corpus_status_labels=CORPUS_STATUS_LABELS,
        )

    base_where = ["f.audit_run_id = ?"]
    base_params = [run["id"]]
    if severity:
        base_where.append("f.severity = ?")
        base_params.append(severity)
    if flag_code:
        base_where.append("f.flag_code = ?")
        base_params.append(flag_code)
    if entity_type:
        base_where.append("f.entity_type = ?")
        base_params.append(entity_type)
    where_sql = " AND ".join(base_where)

    counts = db.execute(
        """SELECT f.flag_code, f.severity, f.entity_type, COUNT(*) AS cnt
           FROM data_quality_flags f
           WHERE f.audit_run_id = ?
           GROUP BY f.flag_code, f.severity, f.entity_type
           ORDER BY cnt DESC, f.flag_code""",
        (run["id"],),
    ).fetchall()
    flag_codes = db.execute(
        """SELECT DISTINCT flag_code FROM data_quality_flags
           WHERE audit_run_id = ? ORDER BY flag_code""",
        (run["id"],),
    ).fetchall()
    total = db.execute(
        f"SELECT COUNT(*) FROM data_quality_flags f WHERE {where_sql}",
        base_params,
    ).fetchone()[0]
    flags = db.execute(
        f"""SELECT f.*, p.name AS person_name, p.id AS person_id,
                   e.event_date, e.event_code, ep.name AS event_person_name,
                   ep.id AS event_person_id
            FROM data_quality_flags f
            LEFT JOIN persons p ON f.entity_type = 'person' AND f.entity_id = p.id
            LEFT JOIN events e ON f.entity_type = 'event' AND f.entity_id = e.id
            LEFT JOIN persons ep ON e.person_id = ep.id
            WHERE {where_sql}
            ORDER BY CASE f.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     f.flag_code, f.entity_id
            LIMIT ? OFFSET ?""",
        base_params + [per_page, (page - 1) * per_page],
    ).fetchall()
    flags = [
        {**dict(row), "details_text": localize_details(row["details_json"])}
        for row in flags
    ]
    total_pages = math.ceil(total / per_page) if total else 1
    db.close()
    corpus_report = load_corpus_mapping_report()
    return render_template(
        "quality.html", run=run, runs=runs, counts=counts, flags=flags,
        total=total, page=page, total_pages=total_pages, severity=severity,
        flag_code=flag_code, entity_type=entity_type, flag_codes=flag_codes,
        quality_labels=QUALITY_LABELS, entity_labels=ENTITY_LABELS,
        severity_labels=SEVERITY_LABELS,
        corpus_report=corpus_report,
        corpus_status_labels=CORPUS_STATUS_LABELS,
    )


# ── 预测域：标准化观察终点 ─────────────────────────────────────
@app.route("/domains")
def prediction_domains_dashboard():
    """展示预测域定义、证据规模和缺失语义。"""
    db = get_db()
    rows = db.execute(
        """SELECT d.*, COUNT(o.id) AS person_count,
                  COALESCE(SUM(o.event_count), 0) AS evidence_count,
                  COALESCE(SUM(CASE WHEN o.first_date_start IS NOT NULL THEN 1 ELSE 0 END), 0) AS dated_count
           FROM prediction_domains d
           LEFT JOIN person_prediction_outcomes o
             ON o.domain_code = d.code AND o.rule_version = d.rule_version
           GROUP BY d.code ORDER BY d.code"""
    ).fetchall()
    rule_counts = db.execute(
        """SELECT domain_code, COUNT(*) AS cnt FROM prediction_event_rules
           GROUP BY domain_code ORDER BY domain_code"""
    ).fetchall()
    db.close()
    return render_template(
        "domains.html", domains=rows,
        rule_counts={r["domain_code"]: r["cnt"] for r in rule_counts},
        target_type_labels=TARGET_TYPE_LABELS,
    )


# ── 古籍语料与规则研究 ─────────────────────────────────────────
# 按文件 mtime 缓存 jsonl，避免每次请求重复解析大文件（三命通会 OCR 约 16MB）
_JSONL_CACHE: dict[Path, tuple[float, list]] = {}


def _load_jsonl_cached(path: Path, slim=None) -> list[dict]:
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    cached = _JSONL_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with path.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if slim:
        rows = [slim(row) for row in rows]
    _JSONL_CACHE[path] = (mtime, rows)
    return rows


def load_canon_layers(book: str) -> list[dict]:
    rows = list(_load_jsonl_cached(CANON_LAYERS_DIR / f"{book}_layers.jsonl"))
    # 对齐完成后优先使用逐段模型结果；没有结果才显示卷/品级现代译文。
    # 网页白话 / 模型对齐白话 / 项目自译 分栏保留，状态经 apply_quality_fields 降级。
    aligned_path = CANON_LAYERS_DIR / f"{book}_aligned_layers.jsonl"
    modern_path = CANON_LAYERS_DIR / f"{book}_modern_layers.jsonl"
    generated_path = CANON_LAYERS_DIR / f"{book}_generated_layers.jsonl"
    if aligned_path.exists() and aligned_path.stat().st_size:
        aligned_rows = [
            apply_quality_fields(item, pipeline="align_canon_models")
            for item in _load_jsonl_cached(aligned_path)
        ]
        rows.extend(aligned_rows)
        aligned_keys = {
            (item.get("volume"), item.get("chapter"), item.get("source_file"))
            for item in aligned_rows
        }
        if modern_path.exists():
            rows.extend(
                apply_quality_fields(item, pipeline="process_huayan_modern", force_candidate=True)
                for item in _load_jsonl_cached(modern_path)
                if (item.get("volume"), item.get("chapter"), item.get("source_file")) not in aligned_keys
            )
    elif modern_path.exists():
        rows.extend(
            apply_quality_fields(item, pipeline="process_huayan_modern", force_candidate=True)
            for item in _load_jsonl_cached(modern_path)
        )
    # 项目自己的“小段独立翻译”与网站白话分开保存，均挂回原文段下方。
    if generated_path.exists() and generated_path.stat().st_size:
        rows.extend(
            apply_quality_fields(item, pipeline="translate_huayan_segments")
            for item in _load_jsonl_cached(generated_path)
        )
    related_path = CANON_LAYERS_DIR / f"{book}_related_layers.jsonl"
    if related_path.exists():
        rows.extend(_load_jsonl_cached(related_path))
    return rows


BOOK_SPECS_PATH = BASE_DIR / "numerology" / "canon" / "book_specs.yaml"


def load_book_specs() -> dict:
    """各书现代规格（tier/输入/输出/一句话算法/可验证性）。"""
    if not BOOK_SPECS_PATH.exists():
        return {}
    return yaml.safe_load(BOOK_SPECS_PATH.read_text(encoding="utf-8")).get("specs", {})


def load_related_sources(book: str) -> list[dict]:
    """读取跨书聚合来源；无法精确归卦的材料只在全书层展示。"""
    path = CANON_LAYERS_DIR / f"{book}_related_sources.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("sources", []) if isinstance(data, dict) else []


def canon_layer_stats(segments: list[dict]) -> dict:
    stats = {"total": len(segments), "layers": {}, "low_pending": 0, "chapters": 0}
    chapters = set()
    for seg in segments:
        layer = stats["layers"].setdefault(seg["layer"], {"high": 0, "low": 0})
        layer[seg["confidence"]] += 1
        if seg["confidence"] == "low" and seg["layer"] not in {"站点内容", "相关著作"}:
            stats["low_pending"] += 1
        if seg["chapter"] is not None:
            chapters.add(seg["chapter"])
    stats["chapters"] = len(chapters)
    return stats


def scan_files_for_book(book: str) -> list[Path]:
    """按文件名把本地扫描底本归入对应著作。"""
    if book == "huayan_t0279":
        return sorted(HUAYAN_SCAN_DIR.glob("*.pdf")) if HUAYAN_SCAN_DIR.exists() else []
    prefixes = {
        "ziping_zhenquan": ("ziping_zhenquan",),
        "yuanhai_ziping": ("yuanhai_ziping",),
        "ditiansui": ("di_tian_sui",),
        "sanming_tonghui": ("sanming_tonghui",),
    }
    return [
        pdf for pdf in sorted(CANON_SCAN_DIR.glob("*.pdf"))
        if pdf.stem.startswith(prefixes.get(book, ()))
    ]


def canon_scan_path(filename: str) -> Path | None:
    """在允许的扫描目录中查找文件，避免把任意路径暴露给下载路由。"""
    for directory in (CANON_SCAN_DIR, HUAYAN_SCAN_DIR):
        candidate = directory / filename
        if candidate.is_file() and candidate.parent == directory:
            return candidate
    return None


def ocr_editions() -> list[dict]:
    """扫描 OCR 输出目录，汇总每个版本的页面与识别进度。"""
    editions = []
    if not CANON_OCR_DIR.exists():
        return editions
    for source_dir in sorted(CANON_OCR_DIR.iterdir()):
        if not source_dir.is_dir():
            continue
        pages = sorted((source_dir / "pages").glob("page-*.png"))
        records = load_ocr_records(source_dir.name)
        status_counts = {}
        for record in records:
            status = record.get("ocr_status", "raw")
            status_counts[status] = status_counts.get(status, 0) + 1
        editions.append({
            "source_id": source_dir.name,
            "page_count": len(pages),
            "ocr_count": len(records),
            "status_counts": status_counts,
        })
    return editions


def _slim_ocr_record(record: dict) -> dict:
    """页面展示与对齐只需少数字段；raw_result/blocks 留在磁盘上按需查。"""
    return {
        key: record.get(key)
        for key in ("page_pdf", "text_raw", "ocr_status", "chapter", "input_pdf")
    }


def load_ocr_records(source_id: str) -> list[dict]:
    """读取一个版本的 OCR 记录（瘦身+缓存）；无结果时返回空列表。"""
    return _load_jsonl_cached(
        CANON_OCR_DIR / source_id / "ocr.jsonl", slim=_slim_ocr_record
    )


def load_ocr_manifest(source_id: str) -> dict:
    """读取 OCR 版本清单，确保页面能回溯到唯一 PDF 底本。"""
    path = CANON_OCR_DIR / source_id / "manifest.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_scan_image_records(source_id: str) -> list[dict]:
    """读取独立于 OCR 的扫描页面记录。"""
    path = CANON_SCAN_IMAGES_DIR / source_id / "images.jsonl"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_page_map(source_id: str) -> dict[int, int]:
    """读取人工维护的 PDF 页码→章节映射。"""
    path = CANON_OCR_DIR / source_id / "page_map.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    page_values = raw.get("pages", raw) if isinstance(raw, dict) else {}
    result = {}
    for page, chapter in page_values.items():
        try:
            if isinstance(chapter, dict):
                chapter = chapter.get("chapter")
            result[int(page)] = int(chapter)
        except (TypeError, ValueError):
            continue
    return result


def load_page_chapters(source_id: str) -> dict[int, list[int]]:
    """读取页码对应的全部章节；一页内可能连续排有多个小章节。"""
    path = CANON_OCR_DIR / source_id / "page_map.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    details = raw.get("page_details", {}) if isinstance(raw, dict) else {}
    result = {}
    if isinstance(details, dict):
        for page, detail in details.items():
            try:
                chapters = detail.get("chapters", []) if isinstance(detail, dict) else []
                result[int(page)] = [int(chapter) for chapter in chapters]
            except (TypeError, ValueError):
                continue
    primary = load_page_map(source_id)
    for page, chapter in primary.items():
        result.setdefault(page, [chapter])
    return result


def load_page_mapping_details(source_id: str) -> dict[int, dict]:
    """读取页级映射状态，用于区分自动定位和人工补标注。"""
    path = CANON_OCR_DIR / source_id / "page_map.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    details = raw.get("page_details", {}) if isinstance(raw, dict) else {}
    result = {}
    if isinstance(details, dict):
        for page, detail in details.items():
            if isinstance(detail, dict):
                try:
                    result[int(page)] = detail
                except (TypeError, ValueError):
                    continue
    return result


def record_chapter(record: dict, page_map: dict[int, int]) -> int | None:
    """优先使用 OCR 批次章节，其次使用人工页码映射。"""
    if record.get("chapter") is not None:
        return record["chapter"]
    return page_map.get(int(record["page_pdf"]))


SIMILARITY_CHAR_CAP = 30_000  # 章节级文本远小于此；超过说明在比全书，直接跳过


def comparable_text(text: str) -> str:
    """仅用于定位差异，去掉空白，不做繁简或异体字替换。"""
    return re.sub(r"\s+", "", text or "")


def text_diff_counts(left: str, right: str) -> dict[str, int]:
    """统计两份文本的粗略差异字符数，不自动判定哪一份正确。"""
    counts = {"online_only": 0, "pdf_only": 0, "equal": 0}
    for tag, left_start, left_end, right_start, right_end in difflib.SequenceMatcher(
        None, left, right
    ).get_opcodes():
        if tag == "equal":
            counts["equal"] += left_end - left_start
        elif tag == "delete":
            counts["online_only"] += left_end - left_start
        elif tag == "insert":
            counts["pdf_only"] += right_end - right_start
        else:
            counts["online_only"] += left_end - left_start
            counts["pdf_only"] += right_end - right_start
    return counts


def canon_alignment(book: str, segments: list[dict], chapter: int | None) -> dict:
    """建立互联网文本与 PDF/OCR 的章节级对照摘要。

    字符相似度只用于定位版本差异，不代表校勘结论；没有章节映射时不计算。
    """
    selected = [s for s in segments if chapter is None or s["chapter"] == chapter]
    online_text = "\n".join(
        s["text"] for s in selected
        if s["layer"] not in {"现代白话", "现代释译", "站点内容"}
    )
    editions = []
    ocr_inputs = set()
    prefix = f"{book}_"
    for source_dir in sorted(CANON_OCR_DIR.glob(f"{prefix}*")):
        if not source_dir.is_dir():
            continue
        source_id = source_dir.name
        records = load_ocr_records(source_id)
        manifest = load_ocr_manifest(source_id)
        scan_filename = Path(str(manifest.get("input_pdf") or "")).name
        ocr_inputs.update(
            Path(r["input_pdf"]).name
            for r in records
            if r.get("input_pdf")
        )
        page_map = load_page_map(source_id)
        page_chapters = load_page_chapters(source_id)
        page_details = load_page_mapping_details(source_id)
        has_mapping = bool(page_map) or bool(page_chapters)
        images = sorted((source_dir / "pages").glob("page-*.png"))
        if chapter is None:
            selected_records = records
        else:
            selected_records = [
                r for r in records if chapter in page_chapters.get(
                    int(r["page_pdf"]), [record_chapter(r, page_map)]
                )
            ]
        pages = []
        record_by_page = {int(r["page_pdf"]): r for r in records}
        for image in images:
            number = int(image.stem.rsplit("-", 1)[1])
            record = record_by_page.get(number, {})
            mapped_chapter = record_chapter(record, page_map) if record else page_map.get(number)
            chapter_list = page_chapters.get(number, [mapped_chapter] if mapped_chapter is not None else [])
            if chapter is None or chapter in chapter_list:
                pages.append({
                    "number": number,
                    "image": image.name,
                    "chapter": mapped_chapter,
                    "chapters": chapter_list,
                    "text": record.get("text_raw"),
                    "status": record.get("ocr_status", "未识别"),
                    "mapping_status": page_details.get(number, {}).get("mapping_status"),
                })
        pdf_text = "\n".join(r.get("text_raw", "") for r in selected_records)
        online_norm = comparable_text(online_text)
        pdf_norm = comparable_text(pdf_text)
        similarity = None
        diff_counts = None
        # SequenceMatcher 是 O(n×m)：只在章节级文本量下计算，全书级会卡住页面
        if (
            online_norm and pdf_norm and selected_records
            and len(online_norm) <= SIMILARITY_CHAR_CAP
            and len(pdf_norm) <= SIMILARITY_CHAR_CAP
        ):
            similarity = round(difflib.SequenceMatcher(None, online_norm, pdf_norm).ratio(), 3)
            diff_counts = text_diff_counts(online_norm, pdf_norm)
        if not records and images:
            status = "已渲染，未运行 OCR"
        elif not records:
            status = "未渲染"
        elif chapter is not None and not selected_records:
            status = "本章未定位，待人工复核" if has_mapping else "已 OCR，待章节标注"
        elif not selected_records:
            status = "无 OCR 文字"
        elif any(
            page_details.get(int(record["page_pdf"]), {}).get("mapping_status") == "人工补标注"
            for record in selected_records
        ):
            status = "已补标注，待扫描图复核"
        else:
            status = "已标注，可对照"
        editions.append({
            "source_id": source_id,
            "page_count": len(images),
            "ocr_count": len(records),
            "selected_page_count": len(pages),
            "selected_ocr_count": len(selected_records),
            "status": status,
            "ocr_url": f"/canon/ocr/{source_id}",
            "scan_filename": scan_filename or None,
            "scan_url": f"/canon/scan/{scan_filename}" if scan_filename and canon_scan_path(scan_filename) else None,
            "input_sha256": str(manifest.get("input_sha256") or "")[:12] or None,
            "similarity": similarity,
            "diff_counts": diff_counts,
            "pages": pages,
        })
    for pdf in scan_files_for_book(book):
        if pdf.name in ocr_inputs:
            continue
        image_records = load_scan_image_records(pdf.stem)
        editions.append({
            "source_id": f"scan_{pdf.stem}",
            "scan_filename": pdf.name,
            "scan_url": f"/canon/scan/{pdf.name}",
            "page_count": None,
            "ocr_count": 0,
            "selected_page_count": 0,
            "selected_ocr_count": 0,
            "status": "已记录图片，尚未章节标注/OCR" if image_records else "已有扫描底本，尚未记录图片",
            "similarity": None,
            "diff_counts": None,
            "pages": [
                {
                    "number": row["page_pdf"],
                    "image": row["image"],
                    "image_url": f"/canon/scan-images/{pdf.stem}/pages/{row['image']}",
                    "chapter": row.get("chapter"),
                    "status": row.get("image_status", "recorded"),
                }
                for row in image_records
            ],
        })
    return {
        "chapter": chapter,
        "online_segment_count": len(selected),
        "online_char_count": len(comparable_text(online_text)),
        "online_segments": selected if chapter is not None else [],
        "editions": editions,
    }


@app.route("/canon")
def canon_dashboard():
    """古籍研究总览：语料、分层标注、扫描件与 OCR 进度。"""
    books = []
    specs = load_book_specs()
    for book, config in CANON_BOOKS.items():
        segments = load_canon_layers(book)
        text_file = CANON_PROCESSED_DIR / f"{book}_online.txt"
        books.append({
            "key": book,
            "title": config["title"],
            "system": config.get("system", ""),
            "corpus_group": config.get("corpus_group", "命理语料"),
            "calculation_scope": config.get("calculation_scope", "可进入命理研究流程"),
            "markers": config["commentary_markers"],
            "spec": specs.get(book, {}),
            "stats": canon_layer_stats(segments),
            "text_size": text_file.stat().st_size if text_file.exists() else 0,
        })
    books.sort(key=lambda b: (b["corpus_group"], b["system"], b["key"]))
    scans = [
        {"name": pdf.name, "size_mb": pdf.stat().st_size / 1024 / 1024}
        for directory in (CANON_SCAN_DIR, HUAYAN_SCAN_DIR)
        for pdf in sorted(directory.glob("*.pdf"))
        if directory.exists()
    ]
    schools = []
    if SCHOOLS_DIR.exists():
        for path in sorted(SCHOOLS_DIR.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            rules = data.get("rules", [])
            counts = {}
            for rule in rules:
                key = rule.get("rule_status", "candidate")
                counts[key] = counts.get(key, 0) + 1
            schools.append({"name": path.stem, "total": len(rules), "counts": counts})
    return render_template(
        "canon.html", books=books, scans=scans, editions=ocr_editions(),
        schools=schools, layer_labels=LAYER_LABELS, layer_badges=LAYER_BADGES,
    )


@app.route("/canon/<book>")
def canon_book(book):
    """按章节/层级浏览某本书的分层标注结果。"""
    if book not in CANON_BOOKS:
        abort(404)
    segments = load_canon_layers(book)
    chapter = request.args.get("chapter", type=int)
    layer = request.args.get("layer", "")
    confidence = request.args.get("confidence", "")
    chapters = sorted({s["chapter"] for s in segments if s["chapter"] is not None})
    chapter_directory = []
    for number in chapters:
        chapter_segments = [s for s in segments if s["chapter"] == number]
        chapter_directory.append({
            "number": number,
            "title": next((s.get("chapter_title") for s in chapter_segments if s.get("chapter_title")), None),
            "book_label": next((s.get("book_chapter_label") for s in chapter_segments if s.get("book_chapter_label")), None),
            "original_count": sum(s["layer"] == "原文" for s in chapter_segments),
            "commentary_count": sum(s["layer"] in {"原注", "评注"} for s in chapter_segments),
            "translation_count": sum(
                s["layer"] in {"现代白话", "现代释译"} for s in chapter_segments
            ),
            "related_count": sum(s["layer"] == "相关著作" for s in chapter_segments),
            "site_count": sum(s["layer"] == "站点内容" for s in chapter_segments),
        })
    chapter_segments = [s for s in segments if chapter is not None and s["chapter"] == chapter]
    chapter_title = next(
        (s.get("chapter_title") for s in chapter_segments if s.get("chapter_title")),
        None,
    )
    book_chapter_label = next(
        (s.get("book_chapter_label") for s in chapter_segments if s.get("book_chapter_label")),
        None,
    )
    original_segments = [
        s for s in chapter_segments
        if s["layer"] == "原文" and (not confidence or s["confidence"] == confidence)
    ]
    # 现代译文/释译：段号优先 → 规范化 section_key；禁止“段数相等即顺序对齐”。
    # 华严：阅读时优先挂项目自译（现代释译），网页/对齐白话仍在 auxiliary 分栏。
    inline_layers = {"现代白话", "现代释译"}
    inline_items = [
        s for s in chapter_segments
        if s["layer"] in inline_layers
        and (not confidence or s["confidence"] == confidence)
        and (not layer or layer in inline_layers)
    ]
    if book == "huayan_t0279":
        generated_translations = [
            item for item in inline_items
            if item["layer"] == "现代释译"
        ]
        if generated_translations:
            inline_items = generated_translations
    alignment_result = resolve_inline_alignment(original_segments, inline_items)
    inline_by_original = alignment_result["inline_by_original"]
    unmatched_inline = alignment_result["unmatched_inline"]
    pending_unmapped = alignment_result["pending_unmapped"]
    inline_alignment_pending = bool(pending_unmapped)
    auxiliary_by_layer = {}
    for auxiliary_layer in ("原注", "评注", "现代白话", "现代释译", "相关著作", "站点内容"):
        auxiliary_by_layer[auxiliary_layer] = [
            s for s in chapter_segments
            if s["layer"] == auxiliary_layer
            and (not layer or layer == auxiliary_layer)
            and (not confidence or s["confidence"] == confidence)
        ]
    # 目录视图不渲染版本对照卡片，跳过对齐计算（全书级 difflib 会卡页面十几秒）
    if chapter is not None:
        alignment = canon_alignment(book, segments, chapter)
    else:
        alignment = {
            "chapter": None, "online_segment_count": 0, "online_char_count": 0,
            "online_segments": [], "editions": [],
        }
    return render_template(
        "canon_book.html",
        book=book, title=CANON_BOOKS[book]["title"],
        book_spec=load_book_specs().get(book, {}),
        stats=canon_layer_stats(segments),
        chapters=chapters, chapter=chapter, layer=layer,
        confidence=confidence, chapter_directory=chapter_directory,
        chapter_title=chapter_title, book_chapter_label=book_chapter_label,
        original_segments=original_segments, auxiliary_by_layer=auxiliary_by_layer,
        inline_by_original=inline_by_original, unmatched_inline=unmatched_inline,
        corpus_group=CANON_BOOKS[book].get("corpus_group", "命理语料"),
        calculation_scope=CANON_BOOKS[book].get("calculation_scope", "可进入命理研究流程"),
        layer_labels=LAYER_LABELS, layer_badges=LAYER_BADGES, alignment=alignment,
        related_sources=load_related_sources(book),
        pending_inline_alignment=bool(pending_unmapped),
        corpus_status_labels=CORPUS_STATUS_LABELS,
        inline_align_method=alignment_result.get("method"),
    )


@app.route("/canon/ocr/<source_id>")
def canon_ocr(source_id):
    """展示某个扫描版本的页面图像与 OCR 原始文本。"""
    source_dir = CANON_OCR_DIR / source_id
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", source_id) or not source_dir.is_dir():
        abort(404)
    records_by_page = {int(r["page_pdf"]): r for r in load_ocr_records(source_id)}
    manifest = load_ocr_manifest(source_id)
    page_map = load_page_map(source_id)
    page_chapters = load_page_chapters(source_id)
    all_images = sorted((source_dir / "pages").glob("page-*.png"))
    requested_page = request.args.get("page", type=int)
    if requested_page is not None:
        images = [
            image for image in all_images
            if int(image.stem.rsplit("-", 1)[1]) == requested_page
        ]
        page_index = None
        page_size = 1
    else:
        page_size = min(max(request.args.get("size", 20, type=int), 1), 50)
        page_index = max(request.args.get("index", 1, type=int), 1)
        start = (page_index - 1) * page_size
        images = all_images[start:start + page_size]
    pages = []
    for image in images:
        number = int(image.stem.rsplit("-", 1)[1])
        record = records_by_page.get(number)
        pages.append({
            "number": number,
            "image": image.name,
            "text": record.get("text_raw") if record else None,
            "status": record.get("ocr_status") if record else "未识别",
            "chapter": record_chapter(record, page_map) if record else page_map.get(number),
            "chapters": page_chapters.get(number, []),
        })
    return render_template(
        "canon_ocr.html",
        source_id=source_id,
        pages=pages,
        scan_filename=Path(str(manifest.get("input_pdf") or "")).name or None,
        input_sha256=str(manifest.get("input_sha256") or "")[:12] or None,
        total_pages=len(all_images), page_index=page_index, page_size=page_size,
        requested_page=requested_page,
    )


@app.route("/canon/ocr/<source_id>/pages/<filename>")
def canon_ocr_image(source_id, filename):
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", source_id) or not re.fullmatch(
        r"page-\d+\.png", filename
    ):
        abort(404)
    # OCR 页面会被缩略图和弹窗重复请求；关闭条件缓存，避免开发服务器在
    # 并发 304 响应下出现浏览器 ERR_INVALID_HTTP_RESPONSE。
    return send_from_directory(
        CANON_OCR_DIR / source_id / "pages", filename, conditional=False, max_age=0
    )


@app.route("/canon/scan-images/<source_id>/pages/<filename>")
def canon_scan_image(source_id, filename):
    """提供已记录的扫描页面图片。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", source_id) or not re.fullmatch(
        r"page-\d+\.png", filename
    ):
        abort(404)
    return send_from_directory(
        CANON_SCAN_IMAGES_DIR / source_id / "pages", filename, conditional=False, max_age=0
    )


@app.route("/favicon.ico")
def favicon():
    """避免浏览器自动请求 favicon 时产生无意义的 404。"""
    return "", 204


@app.route("/canon/scan/<filename>")
def canon_scan(filename):
    """在浏览器中打开本地只读扫描 PDF。"""
    if not re.fullmatch(r"[\w.\-]+\.pdf", filename):
        abort(404)
    path = canon_scan_path(filename)
    if path is None:
        abort(404)
    return send_file(path, mimetype="application/pdf", conditional=False, max_age=0)


# ── 规则校勘：candidate → verified ─────────────────────────────
SCHOOLS_DIR = BASE_DIR / "numerology" / "canon" / "schools"
STEM_CHARS = "甲乙丙丁戊己庚辛壬癸"


def _school_path(school: str) -> Path:
    if not re.fullmatch(r"[a-z_]+", school):
        abort(404)
    path = SCHOOLS_DIR / f"{school}.yaml"
    if not path.exists():
        abort(404)
    return path


def load_school_rules(school: str) -> tuple[list[str], dict]:
    """返回（文件头注释行, 规则数据）。"""
    path = _school_path(school)
    text = path.read_text(encoding="utf-8")
    header = [line for line in text.splitlines() if line.startswith("#")]
    return header, yaml.safe_load(text) or {"rules": []}


def save_school_rules(school: str, header: list[str], data: dict) -> None:
    path = _school_path(school)
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
    path.write_text("\n".join(header) + "\n" + body, encoding="utf-8")


@app.route("/canon/rules/<school>")
def canon_rules(school):
    """规则校勘工作台：引文与候选并排，逐条通过/驳回。"""
    _, data = load_school_rules(school)
    status = request.args.get("status", "")
    rules = data.get("rules", [])
    counts = {}
    for rule in rules:
        counts[rule.get("rule_status", "candidate")] = counts.get(
            rule.get("rule_status", "candidate"), 0
        ) + 1
    if status:
        rules = [r for r in rules if r.get("rule_status", "candidate") == status]
    return render_template(
        "canon_rules.html", school=school, rules=rules,
        counts=counts, status=status, total=len(data.get("rules", [])),
    )


@app.route("/canon/rules/<school>/<rule_id>", methods=["POST"])
def canon_rule_review(school, rule_id):
    """写回一条规则的人工校勘结果（verified_stems / 状态 / 备注）。"""
    header, data = load_school_rules(school)
    rule = next((r for r in data.get("rules", []) if r["rule_id"] == rule_id), None)
    if rule is None:
        abort(404)
    action = request.form.get("action", "")
    stems_raw = request.form.get("verified_stems", "")
    stems = [ch for ch in stems_raw if ch in STEM_CHARS]
    note = request.form.get("review_note", "").strip()
    if action == "verify":
        if not stems:
            abort(400)  # 通过校勘必须给出核定用神序列
        rule["verified_stems"] = stems
        rule["verified_order"] = True  # 输入顺序即优先序
        rule["rule_status"] = "verified"
    elif action == "reject":
        rule["rule_status"] = "rejected"
    elif action == "reset":
        rule["rule_status"] = "candidate"
        rule.pop("verified_stems", None)
        rule.pop("verified_order", None)
    else:
        abort(400)
    if note:
        rule["review_note"] = note
    rule["reviewed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    save_school_rules(school, header, data)
    anchor = request.form.get("next_anchor") or rule_id
    return redirect(f"/canon/rules/{school}?status={request.form.get('back_status','')}#{anchor}")


# ── 濒死探索（第二研究层：NDERF 案例库）──────────────────────
NDE_EXPERIENCES_PATH = BASE_DIR / "data" / "processed" / "nderf" / "experiences.jsonl"
NDE_TRANSLATIONS_PATH = BASE_DIR / "data" / "processed" / "nderf" / "translations.jsonl"
NDE_CONCEPTS_PATH = BASE_DIR / "numerology" / "nde" / "concepts.yaml"
NDE_PAGE_SIZE = 12

# 概念证据使用固定颜色，颜色只表达“这是哪个概念的证据”，不表达真伪或强度。
NDE_CONCEPT_COLORS = {
    "scale_illusion": "violet",
    "time_illusion": "blue",
    "interpenetration": "teal",
    "oneness": "green",
    "consciousness_independent": "indigo",
    "direct_knowing": "orange",
    "light_conscious": "yellow",
    "multiple_realms": "purple",
    "love_fundamental": "pink",
    "more_real": "red",
    "no_judgment": "cyan",
    "purpose_order": "lime",
}

# 濒死探索的标签组：标签组只负责组织阅读界面，小标签仍然可以独立筛选。
# “亡灵”是现象标签的专题组，不把它误当作对体验真实性的判断。
NDE_TAG_GROUP_DEFS = (
    {"key": "phenomenon", "name": "现象", "kind": "category"},
    {"key": "idea", "name": "理念", "kind": "concept"},
    {
        "key": "spirit", "name": "亡灵", "kind": "category",
        "keys": {"beings", "deceased", "religious_figure", "god_awareness"},
    },
)
NDE_CATEGORY_COLORS = {
    "obe": "teal", "tunnel": "violet", "bright_light": "yellow",
    "beings": "purple", "deceased": "indigo", "religious_figure": "orange",
    "god_awareness": "orange", "other_world": "blue", "life_review": "green",
    "boundary": "red", "time_distortion": "blue", "heightened_senses": "lime",
    "special_knowledge": "orange", "future_scenes": "pink", "distressing": "red",
    "aftereffects_gifts": "cyan",
}


def load_nde_concepts() -> dict:
    return yaml.safe_load(NDE_CONCEPTS_PATH.read_text(encoding="utf-8"))["concepts"]


NDE_EVIDENCE_ZH_PATH = BASE_DIR / "data" / "processed" / "nderf" / "evidence_zh.jsonl"
NDE_CONCEPTS_V2_PATH = BASE_DIR / "data" / "processed" / "nderf" / "concepts_v2.jsonl"


def load_nde_experiences() -> list[dict]:
    """案例记录合并翻译、概念标注与中文证据映射（各自独立增量文件）。

    概念以 v2（严格判据重标）为准；v2 未覆盖的案例回退首轮标注。
    """
    records = _load_jsonl_cached(NDE_EXPERIENCES_PATH)
    translations = {
        row["slug"]: row for row in _load_jsonl_cached(NDE_TRANSLATIONS_PATH)
    }
    concepts_v2 = {
        row["slug"]: row.get("concepts", {})
        for row in _load_jsonl_cached(NDE_CONCEPTS_V2_PATH)
    }
    evidence_zh = {
        row["slug"]: row.get("concepts_zh", {})
        for row in _load_jsonl_cached(NDE_EVIDENCE_ZH_PATH)
    }
    for record in records:
        extra = translations.get(record["slug"])
        record["translations"] = (
            {"中文": extra.get("zh", "")} if extra and extra.get("zh") else {}
        )
        slug = record["slug"]
        if slug in concepts_v2:
            record["concepts"] = concepts_v2[slug]
        elif extra:
            record["concepts"] = extra.get("concepts", {})
        else:
            record["concepts"] = {}
        record["concepts_zh"] = evidence_zh.get(slug, {})
    return records


def prepare_nde_rows(
    rows: list[dict], concept_specs: dict, focus_concepts: list[str] | None = None
) -> tuple[list[dict], list[dict]]:
    """为列表页准备原文证据高亮；不修改缓存中的原始记录。"""
    focus = set(focus_concepts or [])
    prepared = []
    legend = {}
    for source in rows:
        record = dict(source)
        concept_tags = [
            {
                "key": key,
                "name": concept_specs[key]["name"],
                "evidence": evidence,
                "color": NDE_CONCEPT_COLORS.get(key, "default"),
            }
            for key, evidence in record.get("concepts", {}).items()
            if key in concept_specs and evidence and (not focus or key in focus)
        ]
        record["description_html"] = highlight_evidence(
            record.get("description", ""), concept_tags
        )
        zh_text = (record.get("translations") or {}).get("中文", "")
        zh_tags = [
            {
                "key": key,
                "name": concept_specs[key]["name"],
                "evidence": sentence,
                "color": NDE_CONCEPT_COLORS.get(key, "default"),
            }
            for key, sentence in record.get("concepts_zh", {}).items()
            if key in concept_specs and sentence and (not focus or key in focus)
        ]
        record["translation_zh_html"] = (
            highlight_evidence(zh_text, zh_tags) if zh_text else ""
        )
        record["highlight_tags"] = concept_tags
        for tag in concept_tags + zh_tags:
            legend[tag["key"]] = {
                "key": tag["key"], "name": tag["name"], "color": tag["color"]
            }
        prepared.append(record)
    return prepared, list(legend.values())


def build_nde_tag_groups(
    phenomena: dict, concept_specs: dict, rows: list[dict]
) -> list[dict]:
    """建立固定页眉中的标签组；数量只统计当前现象分类内的案例。"""
    groups = []
    for definition in NDE_TAG_GROUP_DEFS:
        kind = definition["kind"]
        if kind == "concept":
            specs = concept_specs
            keys = list(specs)
            colors = NDE_CONCEPT_COLORS
        else:
            specs = phenomena
            keys = list(definition.get("keys") or specs)
            # “现象”组展示一般现象；亡灵专题组单独展示相关现象。
            if definition["key"] == "phenomenon":
                spirit_keys = next(
                    group.get("keys", set()) for group in NDE_TAG_GROUP_DEFS
                    if group["key"] == "spirit"
                )
                keys = [key for key in keys if key not in spirit_keys]
            colors = NDE_CATEGORY_COLORS
        items = []
        for tag_key in keys:
            spec = specs.get(tag_key)
            if not spec:
                continue
            count = sum(
                tag_key in (record.get("concepts", {}) if kind == "concept" else record.get("categories", {}))
                for record in rows
            )
            if not count:
                continue
            items.append({
                "key": tag_key,
                "value": f"{kind}:{tag_key}",
                "name": spec["name"],
                "color": colors.get(tag_key, "default"),
                "count": count,
            })
        if items:
            groups.append({
                "key": definition["key"],
                "name": definition["name"],
                "tags": items,
            })
    return groups


@app.route("/nde")
def nde_dashboard():
    """濒死探索总览：按现象大类索引全部案例。"""
    experiences = load_nde_experiences()
    phenomena = load_phenomena()
    category_counts = {}
    for record in experiences:
        for key in record.get("categories", {}):
            category_counts[key] = category_counts.get(key, 0) + 1
    classification_counts = {}
    for record in experiences:
        cls = record.get("classification") or "未标注"
        classification_counts[cls] = classification_counts.get(cls, 0) + 1
    categories = [
        {"key": key, "name": spec["name"], "description": spec["description"],
         "count": category_counts.get(key, 0)}
        for key, spec in phenomena.items()
    ]
    categories.sort(key=lambda c: -c["count"])
    concept_specs = load_nde_concepts()
    concept_counts = {}
    for record in experiences:
        for key in record.get("concepts", {}):
            concept_counts[key] = concept_counts.get(key, 0) + 1
    concepts = [
        {"key": key, "name": spec["name"], "description": spec["description"],
         "parallel": spec.get("parallel", ""), "count": concept_counts.get(key, 0)}
        for key, spec in concept_specs.items()
    ]
    concepts.sort(key=lambda c: -c["count"])
    translated = sum(1 for r in experiences if r.get("translations"))
    return render_template(
        "nde.html", total=len(experiences), categories=categories,
        concepts=concepts, translated=translated,
        classification_counts=sorted(
            classification_counts.items(), key=lambda kv: -kv[1]
        )[:12],
    )


@app.route("/nde/category/<key>")
def nde_category(key):
    phenomena = load_phenomena()
    if key not in phenomena:
        abort(404)
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = NDE_PAGE_SIZE
    all_matched = [
        r for r in load_nde_experiences() if key in r.get("categories", {})
    ]
    concept_specs = load_nde_concepts()
    tag_groups = build_nde_tag_groups(phenomena, concept_specs, all_matched)
    valid_tags = {
        item["value"]: item
        for group in tag_groups for item in group["tags"]
    }
    selected_tags = []
    for value in request.args.getlist("tag"):
        if value in valid_tags and value not in selected_tags:
            selected_tags.append(value)
    # 兼容之前已经分享出去的 ?concept=... 链接。
    for concept in request.args.getlist("concept"):
        value = f"concept:{concept}"
        if concept in concept_specs and value in valid_tags and value not in selected_tags:
            selected_tags.append(value)
    selected_concepts = [
        value.split(":", 1)[1]
        for value in selected_tags if value.startswith("concept:")
    ]
    selected_categories = [
        value.split(":", 1)[1]
        for value in selected_tags if value.startswith("category:")
    ]
    matched = (
        [
            r for r in all_matched
            if all(concept in r.get("concepts", {}) for concept in selected_concepts)
            and all(category in r.get("categories", {}) for category in selected_categories)
        ]
        if selected_tags else all_matched
    )
    total = len(matched)
    total_pages = math.ceil(total / per_page) if total else 1
    rows, highlight_legend = prepare_nde_rows(
        matched[(page - 1) * per_page : page * per_page], concept_specs,
        selected_concepts,
    )
    return render_template(
        "nde_category.html", spec=phenomena[key], key=key, rows=rows,
        total=total, page=page, total_pages=total_pages,
        all_total=len(all_matched), highlight_legend=highlight_legend,
        page_size=per_page, tag_groups=tag_groups,
        selected_tags=selected_tags, selected_concepts=selected_concepts,
    )


_EMBED_INDEX: tuple[float, EmbeddingIndex] | None = None


def get_embedding_index() -> EmbeddingIndex | None:
    global _EMBED_INDEX
    if not NDE_MATRIX_PATH.exists():
        return None
    mtime = NDE_MATRIX_PATH.stat().st_mtime
    if _EMBED_INDEX is None or _EMBED_INDEX[0] != mtime:
        _EMBED_INDEX = (mtime, EmbeddingIndex())
    return _EMBED_INDEX[1]


@app.route("/nde/search")
def nde_search():
    """语义检索：自然语言查询 NDE 案例与华严经段落。"""
    query = request.args.get("q", "").strip()
    selected_sources = set(request.args.getlist("src")) & {"nde", "huayan"}
    results, error = [], None
    if query:
        index = get_embedding_index()
        if index is None:
            error = "向量索引尚未构建，请先运行 build_embeddings.py"
        elif not os.environ.get("OPENAI_API_KEY"):
            error = "服务端缺少 OPENAI_API_KEY，无法编码查询"
        else:
            try:
                import numpy as np
                from openai import OpenAI

                client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
                response = client.embeddings.create(
                    model="text-embedding-3-small", input=[query]
                )
                vector = np.array(response.data[0].embedding, dtype="float32")
                results = index.search(vector, k=30, sources=selected_sources or None)
            except Exception as exc:  # noqa: BLE001 —— 查询失败给出页面提示
                error = f"查询失败：{exc}"
    return render_template(
        "nde_search.html", q=query, results=results, error=error,
        selected_sources=selected_sources,
    )


@app.route("/nde/concept/<key>")
def nde_concept(key):
    """概念标签页：表达某一世界观理解的案例索引（含原文证据句）。"""
    concepts = load_nde_concepts()
    if key not in concepts:
        abort(404)
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = NDE_PAGE_SIZE
    matched = [
        r for r in load_nde_experiences() if key in r.get("concepts", {})
    ]
    total = len(matched)
    total_pages = math.ceil(total / per_page) if total else 1
    rows, highlight_legend = prepare_nde_rows(
        matched[(page - 1) * per_page : page * per_page], concepts
    )
    return render_template(
        "nde_concept.html", spec=concepts[key], key=key, rows=rows,
        total=total, page=page, total_pages=total_pages,
        highlight_legend=highlight_legend, page_size=per_page,
    )


def highlight_evidence(text: str, evidences: list[dict]) -> str:
    """在叙述原文中高亮概念证据句（尽力匹配：全句 → 归一化 → 前缀）。

    返回已转义的 HTML；匹配不到的证据句不高亮（模型摘句可能有轻微改写）。
    """
    from markupsafe import escape

    spans = []  # (start, end, name, key, color)
    lowered = text.lower()
    for item in evidences:
        needle = (item.get("evidence") or "").strip().strip('"')
        if len(needle) < 8:
            continue
        pos = lowered.find(needle.lower())
        if pos < 0:  # 退化为前 40 字符前缀匹配
            prefix = needle[:40].lower()
            pos = lowered.find(prefix)
            needle_len = len(prefix) if pos >= 0 else 0
        else:
            needle_len = len(needle)
        if pos >= 0 and needle_len:
            spans.append((
                pos, pos + needle_len, item["name"],
                item.get("key", "default"), item.get("color", "default"),
            ))
    spans.sort()
    merged, last_end = [], -1
    for start, end, name, concept_key, color in spans:
        if start >= last_end:  # 忽略重叠区间
            merged.append((start, end, name, concept_key, color))
            last_end = end
    parts, cursor = [], 0
    for start, end, name, concept_key, color in merged:
        parts.append(str(escape(text[cursor:start])))
        safe_key = re.sub(r"[^A-Za-z0-9_-]", "", str(concept_key)) or "default"
        color_attrs = (
            f' data-concept="{escape(safe_key)}" data-color="{escape(color)}"'
            if safe_key != "default" else ""
        )
        parts.append(
            f'<mark class="ev-mark" title="概念证据：{escape(name)}"'
            f'{color_attrs}>'
            f"{escape(text[start:end])}</mark>"
        )
        cursor = end
    parts.append(str(escape(text[cursor:])))
    return "".join(parts)


def tag_qa_rows(qa: list[dict], phenomena: dict) -> dict[int, list[str]]:
    """标出触发了现象分类的问卷行 → {行号: [现象名…]}。"""
    from numerology.nde.parser import _is_positive

    tagged: dict[int, list[str]] = {}
    for key, spec in phenomena.items():
        for rule in spec["match"]:
            needle = rule["question_contains"].lower()
            for i, pair in enumerate(qa):
                if needle in pair["q"].lower() and _is_positive(
                    pair["a"], rule.get("positive_contains")
                ):
                    tagged.setdefault(i, []).append(spec["name"])
                    break
            else:
                continue
            break
    return tagged


@app.route("/nde/experience/<slug>")
def nde_experience(slug):
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", slug):
        abort(404)
    record = next(
        (r for r in load_nde_experiences() if r["slug"] == slug), None
    )
    if record is None:
        abort(404)
    phenomena = load_phenomena()
    tags = [
        {"key": key, "name": phenomena[key]["name"], "evidence": evidence}
        for key, evidence in record.get("categories", {}).items()
        if key in phenomena
    ]
    concept_specs = load_nde_concepts()
    concept_tags = [
        {"key": key, "name": concept_specs[key]["name"], "evidence": evidence,
         "color": NDE_CONCEPT_COLORS.get(key, "default")}
        for key, evidence in record.get("concepts", {}).items()
        if key in concept_specs
    ]
    description_html = highlight_evidence(record["description"], concept_tags)
    zh_text = (record.get("translations") or {}).get("中文", "")
    zh_tags = [
        {"key": key, "name": concept_specs[key]["name"], "evidence": sentence,
         "color": NDE_CONCEPT_COLORS.get(key, "default")}
        for key, sentence in record.get("concepts_zh", {}).items()
        if key in concept_specs and sentence
    ]
    translation_zh_html = highlight_evidence(zh_text, zh_tags) if zh_text else ""
    qa_tags = tag_qa_rows(record.get("qa", []), phenomena)
    return render_template(
        "nde_experience.html", r=record, tags=tags, concept_tags=concept_tags,
        description_html=description_html, translation_zh_html=translation_zh_html,
        highlight_legend=[
            {"key": key, "name": concept_specs[key]["name"],
             "color": NDE_CONCEPT_COLORS.get(key, "default")}
            for key in record.get("concepts", {}) if key in concept_specs
        ],
        qa_tags=qa_tags,
    )


# ── 研究文档与经验总结 ─────────────────────────────────────────
def list_docs() -> list[dict]:
    docs = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        title = path.stem
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        docs.append({"name": path.stem, "title": title,
                     "size_kb": path.stat().st_size / 1024})
    return docs


@app.route("/docs")
def docs_index():
    return render_template("docs.html", docs=list_docs())


@app.route("/docs/<name>")
def doc_page(name):
    known = {doc["name"] for doc in list_docs()}
    if name not in known:
        abort(404)
    text = (DOCS_DIR / f"{name}.md").read_text(encoding="utf-8")
    # 文档间相对链接改写到本站路由
    text = re.sub(r"\]\(([^)]+)\.md\)", r"](/docs/\1)", text)
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "toc"])
    return render_template("doc_page.html", name=name, body=body)


# ── API：统计数据 (JSON) ────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    db = get_db()
    element_dist = db.execute("""
        SELECT day_master_element as elem, COUNT(*) as cnt
        FROM bazi GROUP BY elem ORDER BY cnt DESC
    """).fetchall()
    db.close()
    return jsonify([dict(r) for r in element_dist])


if __name__ == "__main__":
    app.run(
        debug=False,
        host=os.environ.get("NUMEROLOGY_HOST", "0.0.0.0"),
        port=int(os.environ.get("NUMEROLOGY_PORT", "8898")),
    )
