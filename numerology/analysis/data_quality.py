"""人物数据质量审计。

审计不删除或修改原始记录；可选择将问题汇总为报告并持久化为逐条标签。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class QualityIssue:
    """一类质量问题的聚合结果。"""

    code: str
    source: str
    severity: str
    count: int
    description: str


@dataclass(frozen=True)
class QualityFlagRule:
    """一条可落到具体实体的质量规则。"""

    code: str
    entity_type: str
    source: str
    severity: str
    description: str
    sql: str
    params: tuple[Any, ...] = ()


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    """执行计数查询。"""
    return int(conn.execute(sql, params).fetchone()[0])


def _add(
    issues: list[QualityIssue],
    conn: sqlite3.Connection,
    code: str,
    source: str,
    severity: str,
    description: str,
    sql: str,
    params: tuple[Any, ...] = (),
) -> None:
    """执行一项审计并记录非零结果。"""
    count = _count(conn, sql, params)
    if count:
        issues.append(QualityIssue(code, source, severity, count, description))


def audit_database(
    conn: sqlite3.Connection,
    current_year: int | None = None,
) -> tuple[list[dict[str, Any]], list[QualityIssue]]:
    """审计规范化人物库，返回摘要和问题列表。"""
    year = current_year or datetime.now().year
    summaries = [
        dict(row)
        for row in conn.execute(
            """
            SELECT source,
                   COUNT(*) AS persons,
                   SUM(name IS NULL OR TRIM(name) = '') AS missing_name,
                   SUM(gender IS NULL) AS missing_gender,
                   SUM(birth_year IS NULL) AS missing_birth_year,
                   SUM(birth_time IS NULL OR birth_time = '') AS missing_birth_time,
                   SUM(death_year IS NULL) AS missing_death_year
            FROM persons
            GROUP BY source
            ORDER BY source
            """
        ).fetchall()
    ]

    issues: list[QualityIssue] = []

    _add(
        issues,
        conn,
        "blank_name",
        "all",
        "error",
        "人物姓名为空",
        "SELECT COUNT(*) FROM persons WHERE name IS NULL OR TRIM(name) = ''",
    )
    _add(
        issues,
        conn,
        "non_person_entry",
        "all",
        "warning",
        "来源记录不是人物类型，应排除出人物分析",
        "SELECT COUNT(*) FROM persons WHERE entry_type != 'person'",
    )
    _add(
        issues,
        conn,
        "future_birth_year",
        "all",
        "error",
        f"出生年份晚于当前年份 {year}",
        "SELECT COUNT(*) FROM persons WHERE birth_year > ?",
        (year,),
    )
    _add(
        issues,
        conn,
        "death_before_birth",
        "all",
        "error",
        "死亡年份早于出生年份",
        """
        SELECT COUNT(*) FROM persons
        WHERE birth_year IS NOT NULL AND death_year IS NOT NULL
          AND death_year < birth_year
        """,
    )
    _add(
        issues,
        conn,
        "lifespan_over_120",
        "all",
        "warning",
        "出生/死亡年份推导寿命超过 120 年，需检查日期精度或纪年换算",
        """
        SELECT COUNT(*) FROM persons
        WHERE birth_year IS NOT NULL AND death_year IS NOT NULL
          AND death_year - birth_year > 120
        """,
    )
    _add(
        issues,
        conn,
        "latitude_out_of_range",
        "all",
        "error",
        "纬度不在 [-90, 90] 范围内",
        """
        SELECT COUNT(*) FROM persons
        WHERE birth_lat IS NOT NULL AND (birth_lat < -90 OR birth_lat > 90)
        """,
    )
    _add(
        issues,
        conn,
        "longitude_out_of_range",
        "all",
        "error",
        "经度不在 [-180, 180] 范围内",
        """
        SELECT COUNT(*) FROM persons
        WHERE birth_lon IS NOT NULL AND (birth_lon < -180 OR birth_lon > 180)
        """,
    )
    _add(
        issues,
        conn,
        "birth_time_invalid",
        "all",
        "error",
        "出生时分超出合法范围",
        """
        SELECT COUNT(*) FROM persons
        WHERE (birth_hour IS NOT NULL AND (birth_hour < 0 OR birth_hour > 23))
           OR (birth_minute IS NOT NULL AND (birth_minute < 0 OR birth_minute > 59))
        """,
    )

    _add(
        issues,
        conn,
        "adb_birth_year_out_of_scope",
        "adb",
        "warning",
        "ADB 出生年份不在本项目预期的 1500—当前年份范围内",
        """
        SELECT COUNT(*) FROM persons
        WHERE source = 'adb' AND (birth_year < 1500 OR birth_year > ?)
        """,
        (year,),
    )
    _add(
        issues,
        conn,
        "adb_coordinate_out_of_range",
        "adb",
        "error",
        "ADB 经纬度解析后越界，疑似度分秒解析错误",
        """
        SELECT COUNT(*) FROM persons
        WHERE source = 'adb'
          AND ((birth_lat IS NOT NULL AND (birth_lat < -90 OR birth_lat > 90))
            OR (birth_lon IS NOT NULL AND (birth_lon < -180 OR birth_lon > 180)))
        """,
    )
    _add(
        issues,
        conn,
        "adb_missing_birth_time",
        "adb",
        "warning",
        "ADB 没有出生时间，不能用于四柱/时柱分析",
        """
        SELECT COUNT(*) FROM persons
        WHERE source = 'adb' AND (birth_hour IS NULL OR birth_time IS NULL OR birth_time = '')
        """,
    )
    _add(
        issues,
        conn,
        "adb_unknown_rodden_rating",
        "adb",
        "warning",
        "ADB Rodden 评级不在已知枚举中",
        """
        SELECT COUNT(*) FROM persons
        WHERE source = 'adb'
          AND rodden_rating IS NOT NULL
          AND rodden_rating NOT IN ('AA', 'A', 'B', 'C', 'DD', 'X', 'XX', 'AAX', 'AX', 'BX', 'CX', 'DX')
        """,
    )
    _add(
        issues,
        conn,
        "cbdb_extreme_year",
        "cbdb",
        "warning",
        "CBDB 年份超出当前研究可解释范围，需回查纪年或推定规则",
        """
        SELECT COUNT(*) FROM persons
        WHERE source = 'cbdb'
          AND ((birth_year IS NOT NULL AND (birth_year < -1000 OR birth_year > ?))
            OR (death_year IS NOT NULL AND (death_year < -1000 OR death_year > ?)))
        """,
        (year, year),
    )
    _add(
        issues,
        conn,
        "cbdb_unknown_name",
        "cbdb",
        "warning",
        "CBDB 人名为未详，不能用于可靠的人物实体对齐",
        "SELECT COUNT(*) FROM persons WHERE source = 'cbdb' AND name = '未詳'",
    )

    _add(
        issues,
        conn,
        "partial_event_date",
        "adb",
        "warning",
        "事件日期含 00 月或 00 日，只能按日期区间处理",
        """
        SELECT COUNT(*) FROM events
        WHERE event_date LIKE '%-00-%' OR event_date LIKE '%-00'
        """,
    )
    _add(
        issues,
        conn,
        "malformed_event_date",
        "all",
        "error",
        "事件日期不是 YYYY-MM-DD 基本格式",
        """
        SELECT COUNT(*) FROM events
        WHERE event_date IS NULL OR event_date = ''
           OR event_date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        """,
    )
    _add(
        issues,
        conn,
        "invalid_bazi_pillar",
        "all",
        "error",
        "八字柱不是两个汉字，计算结果需要回查",
        """
        SELECT COUNT(*) FROM bazi
        WHERE length(year_pillar) != 2
           OR length(month_pillar) != 2
           OR length(day_pillar) != 2
           OR (time_pillar IS NOT NULL AND length(time_pillar) != 2)
        """,
    )
    _add(
        issues,
        conn,
        "bazi_without_time",
        "all",
        "warning",
        "标记为完整四柱但人物缺少出生时刻",
        """
        SELECT COUNT(*) FROM bazi b
        JOIN persons p ON p.id = b.person_id
        WHERE b.has_time_pillar = 1
          AND (p.birth_hour IS NULL OR p.birth_minute IS NULL)
        """,
    )
    _add(
        issues,
        conn,
        "bazi_for_non_adb",
        "all",
        "warning",
        "非 ADB 来源产生了四柱结果，需要确认是否误用了不完整日期",
        """
        SELECT COUNT(*) FROM bazi b
        JOIN persons p ON p.id = b.person_id
        WHERE p.source != 'adb'
        """,
    )
    _add(
        issues,
        conn,
        "orphan_source_record",
        "all",
        "error",
        "来源记录没有对应的规范化人物",
        "SELECT COUNT(*) FROM source_records WHERE person_id IS NULL",
    )
    _add(
        issues,
        conn,
        "invalid_fact_interval",
        "all",
        "error",
        "事实区间的开始日期晚于结束日期",
        """
        SELECT COUNT(*) FROM birth_facts
        WHERE date_start IS NOT NULL AND date_end IS NOT NULL
          AND date_start > date_end
        """,
    )

    return summaries, issues


def _flag_rules(current_year: int) -> list[QualityFlagRule]:
    """返回逐条记录审计规则。

    每条查询必须返回 ``entity_id`` 和 ``source`` 两列，其余列会保存到
    ``details_json``，以便后续回查而不修改原始人物数据。
    """
    return [
        QualityFlagRule(
            "blank_name", "person", "all", "error", "人物姓名为空",
            """SELECT id AS entity_id, source, name, source_id
               FROM persons WHERE name IS NULL OR TRIM(name) = ''""",
        ),
        QualityFlagRule(
            "non_person_entry", "person", "all", "warning", "来源记录不是人物类型",
            """SELECT id AS entity_id, source, source_id, entry_type, name
               FROM persons WHERE entry_type != 'person'""",
        ),
        QualityFlagRule(
            "future_birth_year", "person", "all", "error",
            f"出生年份晚于当前年份 {current_year}",
            """SELECT id AS entity_id, source, source_id, name, birth_year
               FROM persons WHERE birth_year > ?""",
            (current_year,),
        ),
        QualityFlagRule(
            "death_before_birth", "person", "all", "error", "死亡年份早于出生年份",
            """SELECT id AS entity_id, source, source_id, name, birth_year, death_year
               FROM persons
               WHERE birth_year IS NOT NULL AND death_year IS NOT NULL
                 AND death_year < birth_year""",
        ),
        QualityFlagRule(
            "lifespan_over_120", "person", "all", "warning", "推导寿命超过 120 年",
            """SELECT id AS entity_id, source, source_id, name, birth_year, death_year,
                      death_year - birth_year AS lifespan_years
               FROM persons
               WHERE birth_year IS NOT NULL AND death_year IS NOT NULL
                 AND death_year - birth_year > 120""",
        ),
        QualityFlagRule(
            "latitude_out_of_range", "person", "all", "error", "纬度不在 [-90, 90] 范围内",
            """SELECT id AS entity_id, source, source_id, name, birth_lat
               FROM persons
               WHERE birth_lat IS NOT NULL AND (birth_lat < -90 OR birth_lat > 90)""",
        ),
        QualityFlagRule(
            "longitude_out_of_range", "person", "all", "error", "经度不在 [-180, 180] 范围内",
            """SELECT id AS entity_id, source, source_id, name, birth_lon
               FROM persons
               WHERE birth_lon IS NOT NULL AND (birth_lon < -180 OR birth_lon > 180)""",
        ),
        QualityFlagRule(
            "birth_time_invalid", "person", "all", "error", "出生时分超出合法范围",
            """SELECT id AS entity_id, source, source_id, name, birth_time,
                      birth_hour, birth_minute
               FROM persons
               WHERE (birth_hour IS NOT NULL AND (birth_hour < 0 OR birth_hour > 23))
                  OR (birth_minute IS NOT NULL AND (birth_minute < 0 OR birth_minute > 59))""",
        ),
        QualityFlagRule(
            "adb_birth_year_out_of_scope", "person", "adb", "warning",
            "ADB 出生年份不在 1500—当前年份范围内",
            """SELECT id AS entity_id, source, source_id, name, birth_year
               FROM persons
               WHERE source = 'adb' AND (birth_year < 1500 OR birth_year > ?)""",
            (current_year,),
        ),
        QualityFlagRule(
            "adb_coordinate_out_of_range", "person", "adb", "error",
            "ADB 经纬度解析后越界",
            """SELECT id AS entity_id, source, source_id, name, birth_lat, birth_lon,
                      birth_lat_raw, birth_lon_raw
               FROM persons
               WHERE source = 'adb'
                 AND ((birth_lat IS NOT NULL AND (birth_lat < -90 OR birth_lat > 90))
                   OR (birth_lon IS NOT NULL AND (birth_lon < -180 OR birth_lon > 180)))""",
        ),
        QualityFlagRule(
            "adb_missing_birth_time", "person", "adb", "warning",
            "ADB 没有出生时间，不能用于时柱分析",
            """SELECT id AS entity_id, source, source_id, name, birth_date, birth_time,
                      time_unknown, time_accuracy
               FROM persons
               WHERE source = 'adb' AND (birth_hour IS NULL OR birth_time IS NULL OR birth_time = '')""",
        ),
        QualityFlagRule(
            "adb_unknown_rodden_rating", "person", "adb", "warning",
            "ADB Rodden 评级不在已知枚举中",
            """SELECT id AS entity_id, source, source_id, name, rodden_rating
               FROM persons
               WHERE source = 'adb' AND rodden_rating IS NOT NULL
                 AND rodden_rating NOT IN ('AA', 'A', 'B', 'C', 'DD', 'X', 'XX',
                                           'AAX', 'AX', 'BX', 'CX', 'DX')""",
        ),
        QualityFlagRule(
            "cbdb_extreme_year", "person", "cbdb", "warning", "CBDB 年份超出当前可解释范围",
            """SELECT id AS entity_id, source, source_id, name, birth_year, death_year
               FROM persons
               WHERE source = 'cbdb'
                 AND ((birth_year IS NOT NULL AND (birth_year < -1000 OR birth_year > ?))
                   OR (death_year IS NOT NULL AND (death_year < -1000 OR death_year > ?)))""",
            (current_year, current_year),
        ),
        QualityFlagRule(
            "cbdb_unknown_name", "person", "cbdb", "warning", "CBDB 人名为未详",
            """SELECT id AS entity_id, source, source_id, name
               FROM persons WHERE source = 'cbdb' AND name = '未詳'""",
        ),
        QualityFlagRule(
            "partial_event_date", "event", "adb", "warning", "事件日期含未知月或日",
            """SELECT e.id AS entity_id, p.source, e.person_id, e.event_code, e.event_date
               FROM events e JOIN persons p ON p.id = e.person_id
               WHERE e.event_date LIKE '%-00-%' OR e.event_date LIKE '%-00'""",
        ),
        QualityFlagRule(
            "malformed_event_date", "event", "all", "error", "事件日期不是 YYYY-MM-DD 格式",
            """SELECT e.id AS entity_id, p.source, e.person_id, e.event_code, e.event_date
               FROM events e JOIN persons p ON p.id = e.person_id
               WHERE e.event_date IS NULL OR e.event_date = ''
                  OR e.event_date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'""",
        ),
        QualityFlagRule(
            "invalid_bazi_pillar", "person", "all", "error", "八字柱不是两个干支字符",
            """SELECT b.person_id AS entity_id, p.source, p.source_id, p.name,
                      b.year_pillar, b.month_pillar, b.day_pillar, b.time_pillar
               FROM bazi b JOIN persons p ON p.id = b.person_id
               WHERE length(b.year_pillar) != 2 OR length(b.month_pillar) != 2
                  OR length(b.day_pillar) != 2
                  OR (b.time_pillar IS NOT NULL AND length(b.time_pillar) != 2)""",
        ),
        QualityFlagRule(
            "bazi_without_time", "person", "all", "warning", "完整四柱标记与人物出生时刻不一致",
            """SELECT b.person_id AS entity_id, p.source, p.source_id, p.name,
                      b.has_time_pillar, p.birth_time, p.birth_hour, p.birth_minute
               FROM bazi b JOIN persons p ON p.id = b.person_id
               WHERE b.has_time_pillar = 1
                 AND (p.birth_hour IS NULL OR p.birth_minute IS NULL)""",
        ),
        QualityFlagRule(
            "bazi_for_non_adb", "person", "all", "warning", "非 ADB 来源产生了八字结果",
            """SELECT b.person_id AS entity_id, p.source, p.source_id, p.name,
                      b.has_time_pillar
               FROM bazi b JOIN persons p ON p.id = b.person_id
               WHERE p.source != 'adb'""",
        ),
        QualityFlagRule(
            "orphan_source_record", "source_record", "all", "error", "来源记录没有规范化人物",
            """SELECT id AS entity_id, source, source_id, snapshot_id, raw_key
               FROM source_records WHERE person_id IS NULL""",
        ),
        QualityFlagRule(
            "invalid_fact_interval", "birth_fact", "all", "error", "出生事实区间开始晚于结束",
            """SELECT bf.id AS entity_id, p.source, bf.person_id, bf.source_record_id,
                      bf.date_start, bf.date_end, bf.raw_year, bf.raw_month, bf.raw_day
               FROM birth_facts bf JOIN persons p ON p.id = bf.person_id
               WHERE bf.date_start IS NOT NULL AND bf.date_end IS NOT NULL
                 AND bf.date_start > bf.date_end""",
        ),
    ]


def persist_quality_flags(
    conn: sqlite3.Connection,
    current_year: int | None = None,
    rule_version: str = "2026-07-31",
) -> tuple[int, int]:
    """执行逐实体审计并持久化标签，返回 ``(audit_run_id, flag_count)``。

    历史审计运行不会被覆盖；同一个运行内通过唯一约束避免重复标签。
    """
    year = current_year or datetime.now().year
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    cursor = conn.execute(
        """INSERT INTO quality_audit_runs(rule_version, checked_at, current_year, flag_count)
           VALUES (?, ?, ?, 0)""",
        (rule_version, checked_at, year),
    )
    run_id = int(cursor.lastrowid)
    flag_count = 0

    for rule in _flag_rules(year):
        for row in conn.execute(rule.sql, rule.params):
            details = {
                key: row[key]
                for key in row.keys()
                if key not in {"entity_id", "source"} and row[key] is not None
            }
            conn.execute(
                """INSERT OR IGNORE INTO data_quality_flags(
                       audit_run_id, entity_type, entity_id, source, flag_code,
                       severity, details_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    rule.entity_type,
                    row["entity_id"],
                    row["source"] if rule.source == "all" else rule.source,
                    rule.code,
                    rule.severity,
                    json.dumps(details, ensure_ascii=False, default=str),
                    checked_at,
                ),
            )
            flag_count += 1

    conn.execute(
        "UPDATE quality_audit_runs SET flag_count = ? WHERE id = ?",
        (flag_count, run_id),
    )
    conn.commit()
    return run_id, flag_count


def render_markdown(
    summaries: list[dict[str, Any]],
    issues: list[QualityIssue],
    generated_at: str | None = None,
) -> str:
    """将审计结果渲染为 Markdown。"""
    timestamp = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# 数据质量审计报告",
        "",
        f"生成时间：{timestamp}",
        "",
        "## 来源摘要",
        "",
        "| 来源 | 人物数 | 缺少姓名 | 缺少性别 | 缺少出生年 | 缺少出生时刻 | 缺少死亡年 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            "| {source} | {persons} | {missing_name} | {missing_gender} | "
            "{missing_birth_year} | {missing_birth_time} | {missing_death_year} |".format(**row)
        )

    lines.extend(
        [
            "",
            "## 问题清单",
            "",
            "审计只标记问题，不自动删除原始记录。`error` 表示格式或逻辑错误，"
            "`warning` 表示缺失、精度不足或来源语义不确定。",
            "",
            "| 级别 | 来源 | 检查项 | 数量 | 说明 |",
            "|---|---|---|---:|---|",
        ]
    )
    if issues:
        for issue in issues:
            lines.append(
                f"| {issue.severity} | {issue.source} | {issue.code} | "
                f"{issue.count} | {issue.description} |"
            )
    else:
        lines.append("| — | — | — | 0 | 未发现问题 |")
    lines.extend(
        [
            "",
            "## 使用原则",
            "",
            "- `error`：进入正式分析前必须修复或排除。",
            "- `warning`：保留记录，但作为协变量、分层条件或敏感性分析标签。",
            "- 来源本身的日期推定、名人选择偏差和事件记录偏差，不应被简单视为格式错误。",
        ]
    )
    return "\n".join(lines) + "\n"
