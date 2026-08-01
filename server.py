#!/usr/bin/env python3
"""Web server for browsing numerology data."""

import math
import json
import os
import re
import sqlite3
from pathlib import Path

import markdown
from flask import Flask, abort, render_template, request, jsonify, send_from_directory

from process_canon_layers import BOOKS as CANON_BOOKS

app = Flask(__name__, template_folder="templates")
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "numerology.db"
DOCS_DIR = BASE_DIR / "docs"
CANON_PROCESSED_DIR = BASE_DIR / "data" / "processed" / "canon"
CANON_LAYERS_DIR = CANON_PROCESSED_DIR / "layers"
CANON_OCR_DIR = CANON_PROCESSED_DIR / "ocr"
CANON_SCAN_DIR = BASE_DIR / "data" / "raw" / "canon" / "wikimedia"

LAYER_LABELS = {
    "原文": "原文（原著正文）",
    "原注": "原注（刘基）",
    "评注": "评注（徐注/任氏曰/眉批）",
    "现代白话": "现代白话（网站译文）",
    "站点内容": "站点内容（不入统计）",
}
LAYER_BADGES = {
    "原文": "badge-water", "原注": "badge-earth", "评注": "badge-yin",
    "现代白话": "badge-wood", "站点内容": "badge-dd",
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
    return render_template(
        "quality.html", run=run, runs=runs, counts=counts, flags=flags,
        total=total, page=page, total_pages=total_pages, severity=severity,
        flag_code=flag_code, entity_type=entity_type, flag_codes=flag_codes,
        quality_labels=QUALITY_LABELS, entity_labels=ENTITY_LABELS,
        severity_labels=SEVERITY_LABELS,
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
def load_canon_layers(book: str) -> list[dict]:
    path = CANON_LAYERS_DIR / f"{book}_layers.jsonl"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def canon_layer_stats(segments: list[dict]) -> dict:
    stats = {"total": len(segments), "layers": {}, "low_pending": 0, "chapters": 0}
    chapters = set()
    for seg in segments:
        layer = stats["layers"].setdefault(seg["layer"], {"high": 0, "low": 0})
        layer[seg["confidence"]] += 1
        if seg["confidence"] == "low" and seg["layer"] != "站点内容":
            stats["low_pending"] += 1
        if seg["chapter"] is not None:
            chapters.add(seg["chapter"])
    stats["chapters"] = len(chapters)
    return stats


def ocr_editions() -> list[dict]:
    """扫描 OCR 输出目录，汇总每个版本的页面与识别进度。"""
    editions = []
    if not CANON_OCR_DIR.exists():
        return editions
    for source_dir in sorted(CANON_OCR_DIR.iterdir()):
        if not source_dir.is_dir():
            continue
        pages = sorted((source_dir / "pages").glob("page-*.png"))
        records = []
        ocr_jsonl = source_dir / "ocr.jsonl"
        if ocr_jsonl.exists():
            with ocr_jsonl.open(encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle]
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


@app.route("/canon")
def canon_dashboard():
    """古籍研究总览：语料、分层标注、扫描件与 OCR 进度。"""
    books = []
    for book, config in CANON_BOOKS.items():
        segments = load_canon_layers(book)
        text_file = CANON_PROCESSED_DIR / f"{book}_online.txt"
        books.append({
            "key": book,
            "title": config["title"],
            "markers": config["commentary_markers"],
            "stats": canon_layer_stats(segments),
            "text_size": text_file.stat().st_size if text_file.exists() else 0,
        })
    scans = [
        {"name": pdf.name, "size_mb": pdf.stat().st_size / 1024 / 1024}
        for pdf in sorted(CANON_SCAN_DIR.glob("*.pdf"))
    ] if CANON_SCAN_DIR.exists() else []
    return render_template(
        "canon.html", books=books, scans=scans, editions=ocr_editions(),
        layer_labels=LAYER_LABELS, layer_badges=LAYER_BADGES,
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
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 50

    chapters = sorted({s["chapter"] for s in segments if s["chapter"] is not None})
    filtered = [
        s for s in segments
        if (chapter is None or s["chapter"] == chapter)
        and (not layer or s["layer"] == layer)
        and (not confidence or s["confidence"] == confidence)
    ]
    total = len(filtered)
    total_pages = math.ceil(total / per_page) if total else 1
    rows = filtered[(page - 1) * per_page : page * per_page]
    return render_template(
        "canon_book.html",
        book=book, title=CANON_BOOKS[book]["title"], stats=canon_layer_stats(segments),
        segments=rows, chapters=chapters, chapter=chapter, layer=layer,
        confidence=confidence, page=page, total=total, total_pages=total_pages,
        layer_labels=LAYER_LABELS, layer_badges=LAYER_BADGES,
    )


@app.route("/canon/ocr/<source_id>")
def canon_ocr(source_id):
    """展示某个扫描版本的页面图像与 OCR 原始文本。"""
    source_dir = CANON_OCR_DIR / source_id
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", source_id) or not source_dir.is_dir():
        abort(404)
    records_by_page = {}
    ocr_jsonl = source_dir / "ocr.jsonl"
    if ocr_jsonl.exists():
        with ocr_jsonl.open(encoding="utf-8") as handle:
            for record in map(json.loads, handle):
                records_by_page[record["page_pdf"]] = record
    pages = []
    for image in sorted((source_dir / "pages").glob("page-*.png")):
        number = int(image.stem.rsplit("-", 1)[1])
        record = records_by_page.get(number)
        pages.append({
            "number": number,
            "image": image.name,
            "text": record.get("text_raw") if record else None,
            "status": record.get("ocr_status") if record else "未识别",
        })
    return render_template("canon_ocr.html", source_id=source_id, pages=pages)


@app.route("/canon/ocr/<source_id>/pages/<filename>")
def canon_ocr_image(source_id, filename):
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", source_id) or not re.fullmatch(
        r"page-\d+\.png", filename
    ):
        abort(404)
    return send_from_directory(CANON_OCR_DIR / source_id / "pages", filename)


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
