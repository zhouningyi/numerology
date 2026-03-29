#!/usr/bin/env python3
"""Web server for browsing numerology data."""

import math
import sqlite3
from pathlib import Path

from flask import Flask, render_template, request, jsonify

app = Flask(__name__, template_folder="templates")
DB_PATH = Path(__file__).parent / "data" / "numerology.db"


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
    stats["with_time"] = db.execute(
        "SELECT COUNT(*) FROM bazi WHERE has_time_pillar=1"
    ).fetchone()[0]

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

    where_sql = " AND ".join(where)

    total = db.execute(
        f"SELECT COUNT(*) FROM persons p LEFT JOIN bazi b ON b.person_id=p.id WHERE {where_sql}",
        params,
    ).fetchone()[0]

    rows = db.execute(
        f"""SELECT p.id, p.name, p.gender, p.birth_date, p.birth_time,
                   p.rodden_rating, p.birth_country,
                   b.year_pillar, b.month_pillar, b.day_pillar, b.time_pillar,
                   b.day_master, b.day_master_element, b.day_master_yinyang,
                   b.has_time_pillar
            FROM persons p
            LEFT JOIN bazi b ON b.person_id = p.id
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
        entry_type=entry_type,
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

    db.close()
    return render_template(
        "person_detail.html",
        person=person,
        bazi=bazi,
        dayun=dayun,
        categories=categories,
        events=events,
    )


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
    app.run(debug=False, host="0.0.0.0", port=8899)
