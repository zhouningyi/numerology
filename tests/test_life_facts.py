"""结构化生平事实生成测试。"""

import json

from extract_life_facts import extract_life_facts
from numerology.db.schema import init_db


def test_structured_sources_become_traceable_life_facts(tmp_path):
    """事件和分类应保留来源表、来源 ID 与抽取方法。"""
    db_path = tmp_path / "life_facts.db"
    conn = init_db(db_path)
    try:
        person_id = conn.execute(
            "INSERT INTO persons(source, source_id, name) VALUES ('adb', 'p1', '测试人物')"
        ).lastrowid
        event_id = conn.execute(
            """INSERT INTO events(person_id, event_code, event_date, event_notes)
               VALUES (?, 'Relationship : Marriage', '2000-00-00', '结婚')""",
            (person_id,),
        ).lastrowid
        conn.execute(
            """INSERT INTO events_normalized(
                   event_id, person_id, source, event_type, event_subtype,
                   date_start, date_end, date_precision, event_notes,
                   quality_status, quality_flags
               ) VALUES (?, ?, 'adb', 'Relationship', 'Marriage',
                         '2000-01-01', '2000-12-31', 9, '结婚', 'warning',
                         '["year_precision"]')""",
            (event_id, person_id),
        )
        conn.execute(
            "INSERT INTO categories(person_id, category, cat_type) VALUES (?, 'Vocation : Writers', 'occupation')",
            (person_id,),
        )
        conn.commit()
    finally:
        conn.close()

    event_count, category_count = extract_life_facts(db_path)
    assert (event_count, category_count) == (1, 1)

    conn = init_db(db_path)
    try:
        rows = conn.execute(
            """SELECT fact_type, fact_subtype, source_table, source_id,
                      extraction_method, review_status, metadata_json
               FROM biography_facts ORDER BY fact_type"""
        ).fetchall()
        assert tuple(rows[0][:6]) == (
            "category", "occupation", "categories", "1",
            "structured_category", "pending",
        )
        assert tuple(rows[1][:6]) == (
            "event", "Marriage", "events_normalized", "1",
            "structured_event", "accepted",
        )
        assert json.loads(rows[1][6])["quality_status"] == "warning"
    finally:
        conn.close()
