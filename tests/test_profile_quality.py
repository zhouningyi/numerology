"""统一分析质量层测试。"""

import json

from numerology.db.schema import init_db
from profile_quality import profile_quality


def test_native_ratings_are_kept_separate_from_analysis_tier(tmp_path):
    db_path = tmp_path / "quality_profile.db"
    conn = init_db(db_path)
    try:
        conn.execute(
            """INSERT INTO persons(
                   source, source_id, entry_type, name, birth_date,
                   birth_year, birth_month, birth_day, birth_hour,
                   birth_minute, birth_time, rodden_rating
               ) VALUES ('adb', 'a1', 'person', 'ADB人物', '1980-01-02',
                         1980, 1, 2, 12, 30, '12:30', 'AA')"""
        )
        conn.execute(
            """INSERT INTO persons(
                   source, source_id, entry_type, name, birth_year,
                   birth_month, date_precision
               ) VALUES ('cbdb', 'c1', 'person', 'CBDB人物', 1200, 3, 10)"""
        )
        conn.commit()
    finally:
        conn.close()

    counts = profile_quality(db_path)
    assert counts == {"full_bazi": 1, "date_interval": 1}

    conn = init_db(db_path)
    try:
        adb = conn.execute(
            "SELECT * FROM person_quality_profiles WHERE source='adb'"
        ).fetchone()
        cbdb = conn.execute(
            "SELECT * FROM person_quality_profiles WHERE source='cbdb'"
        ).fetchone()
        assert adb["native_rating"] == "AA"
        assert adb["analysis_tier"] == "full_bazi"
        assert cbdb["native_rating"] is None
        assert cbdb["analysis_tier"] == "date_interval"
        assert "cbdb_partial_date" in json.loads(cbdb["quality_flags"])
    finally:
        conn.close()
