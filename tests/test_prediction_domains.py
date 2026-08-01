"""预测域标准化测试。"""

import json

from numerology.analysis.prediction_domains import (
    fact_matches_rule,
    standardize_prediction_domains,
)
from numerology.db.schema import init_db


def test_event_rule_reads_structured_metadata():
    fact = {
        "source": "adb", "fact_type": "event", "fact_subtype": "Marriage",
        "metadata_json": json.dumps({"event_type": "Relationship", "event_subtype": "Marriage"}),
    }
    assert fact_matches_rule(fact, {
        "source": "adb", "fact_type": "event",
        "event_type_regex": "^Relationship$", "event_subtype_regex": "^Marriage$",
    })
    assert not fact_matches_rule(fact, {"source": "cbdb", "fact_type": "event"})


def test_missing_record_is_not_negative(tmp_path):
    conn = init_db(tmp_path / "domains.db")
    try:
        person_id = conn.execute(
            "INSERT INTO persons(source, source_id, name) VALUES ('adb', 'p1', '有事件')"
        ).lastrowid
        conn.execute(
            """INSERT INTO biography_facts(
                   person_id, source, fact_type, fact_subtype, date_start, date_end,
                   date_precision, value_text, source_table, source_id,
                   extraction_method, extractor_version, metadata_json, review_status
               ) VALUES (?, 'adb', 'event', 'Marriage', '2000-01-01', '2000-12-31',
                         9, '结婚', 'events_normalized', 'e1',
                         'structured_event', 'test', ?, 'accepted')""",
            (person_id, json.dumps({"event_type": "Relationship", "event_subtype": "Marriage"})),
        )
        conn.execute(
            "INSERT INTO persons(source, source_id, name) VALUES ('adb', 'p2', '无事件')"
        )
        conn.commit()

        counts = standardize_prediction_domains(conn)
        assert counts["marriage"] == 1
        rows = conn.execute(
            "SELECT person_id, domain_code, outcome_status, first_date_start FROM person_prediction_outcomes"
        ).fetchall()
        assert (person_id, "marriage", "positive", "2000-01-01") in [
            (row[0], row[1], row[2], row[3]) for row in rows
        ]
        assert not any(row[0] != person_id for row in rows)
        assert conn.execute(
            "SELECT COUNT(*) FROM person_prediction_outcomes WHERE outcome_status='negative'"
        ).fetchone()[0] == 0
    finally:
        conn.close()
