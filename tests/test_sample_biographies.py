"""传记分层抽样测试。"""

import json

from numerology.db.schema import init_db
from profile_quality import profile_quality
from sample_biographies import sample_biographies


def test_biography_sample_is_reproducible_and_blinded(tmp_path):
    db_path = tmp_path / "sample.db"
    conn = init_db(db_path)
    try:
        for index in range(6):
            person_id = conn.execute(
                """INSERT INTO persons(
                       source, source_id, name, gender, birth_date, birth_year,
                       birth_month, birth_day, birth_hour, birth_minute,
                       birth_time, rodden_rating, biography
                   ) VALUES ('adb', ?, ?, ?, ?, ?, ?, ?, 12, 30, '12:30', ?, ?)""",
                (
                    f"a{index}", f"人物{index}", "M" if index % 2 else "F",
                    f"198{index}-01-02", 1980 + index, 1, 2,
                    "AA" if index < 3 else "C", f"人物{index}的传记。",
                ),
            ).lastrowid
            conn.execute(
                """INSERT INTO events(person_id, event_code, event_date)
                   VALUES (?, 'Work : Job', '2000-01-01')""",
                (person_id,),
            )
        conn.commit()
    finally:
        conn.close()
    profile_quality(db_path)

    local_path = tmp_path / "local.jsonl"
    blind_path = tmp_path / "blind.jsonl"
    manifest = sample_biographies(db_path, local_path, blind_path, size=4, seed=7)
    assert manifest["actual_size"] == 4
    local = [json.loads(line) for line in local_path.read_text().splitlines()]
    blind = [json.loads(line) for line in blind_path.read_text().splitlines()]
    assert [item["task_id"] for item in local] == [item["task_id"] for item in blind]
    assert "name" in local[0] and "name" not in blind[0]
    assert "rodden_rating" in local[0] and "rodden_rating" not in blind[0]

    second_local = tmp_path / "local2.jsonl"
    second_blind = tmp_path / "blind2.jsonl"
    sample_biographies(db_path, second_local, second_blind, size=4, seed=7)
    assert local_path.read_text() == second_local.read_text()
