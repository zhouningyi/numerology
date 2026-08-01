"""逐条数据质量标签测试。"""

from numerology.analysis.data_quality import persist_quality_flags
from numerology.db.schema import init_db


def test_quality_flags_are_persisted_per_entity(tmp_path):
    """审计应保留运行版本，并把问题落到人物和事件 ID。"""
    conn = init_db(tmp_path / "quality.db")
    try:
        person_id = conn.execute(
            """INSERT INTO persons(
                   source, source_id, name, birth_year, birth_time,
                   birth_hour, birth_minute
               ) VALUES ('adb', 'p1', '测试人物', 2030, NULL, NULL, NULL)"""
        ).lastrowid
        conn.execute(
            """INSERT INTO events(person_id, event_code, event_date, event_notes)
               VALUES (?, 'Test : Partial', '1992-00-00', '测试事件')""",
            (person_id,),
        )
        conn.commit()

        run_id, flag_count = persist_quality_flags(conn, current_year=2026)

        assert flag_count >= 3
        run = conn.execute(
            "SELECT rule_version, current_year, flag_count FROM quality_audit_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        assert tuple(run) == ("2026-07-31", 2026, flag_count)

        flags = conn.execute(
            """SELECT entity_type, entity_id, flag_code, severity
               FROM data_quality_flags WHERE audit_run_id = ?
               ORDER BY flag_code""",
            (run_id,),
        ).fetchall()
        assert ("person", person_id, "future_birth_year", "error") in [tuple(row) for row in flags]
        assert ("person", person_id, "adb_missing_birth_time", "warning") in [
            tuple(row) for row in flags
        ]
        assert any(row[0] == "event" and row[2] == "partial_event_date" for row in flags)

        second_run_id, second_count = persist_quality_flags(
            conn, current_year=2026, rule_version="test-second-run"
        )
        assert second_run_id != run_id
        assert second_count == flag_count
        assert conn.execute("SELECT COUNT(*) FROM quality_audit_runs").fetchone()[0] == 2
    finally:
        conn.close()
