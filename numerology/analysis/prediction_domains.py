"""把生平事实映射为可检验的标准化预测域。"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TAXONOMY_PATH = Path(__file__).with_name("prediction_domains.json")


def load_taxonomy(path: str | Path = TAXONOMY_PATH) -> dict[str, Any]:
    """读取预测域及其匹配规则。"""
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _search(pattern: str | None, value: str | None) -> bool:
    return not pattern or bool(re.search(pattern, value or "", flags=re.IGNORECASE))


def fact_matches_rule(fact: dict[str, Any], rule: dict[str, Any]) -> bool:
    """判断一条 biography_fact 是否命中规则，供脚本和测试复用。"""
    source = rule.get("source")
    if source and source != fact.get("source"):
        return False
    if rule.get("fact_type") != fact.get("fact_type"):
        return False
    if not _search(rule.get("fact_subtype_regex"), fact.get("fact_subtype")):
        return False
    metadata = fact.get("metadata_json") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    return _search(rule.get("event_type_regex"), metadata.get("event_type")) and _search(
        rule.get("event_subtype_regex"), metadata.get("event_subtype") or fact.get("fact_subtype")
    )


def _compile_rules(taxonomy: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rules_by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rule in taxonomy.get("rules", []):
        rules_by_domain[rule["domain_code"]].append(rule)
    return rules_by_domain


def _insert_definitions(conn: sqlite3.Connection, taxonomy: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    version = taxonomy["rule_version"]
    for domain in taxonomy.get("domains", []):
        conn.execute(
            """INSERT INTO prediction_domains
               (code, name, description, target_type, event_unit,
                min_date_precision, absence_policy, source_scope, status,
                rule_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name, description=excluded.description,
                 target_type=excluded.target_type, event_unit=excluded.event_unit,
                 min_date_precision=excluded.min_date_precision,
                 absence_policy=excluded.absence_policy, source_scope=excluded.source_scope,
                 status=excluded.status, rule_version=excluded.rule_version,
                 updated_at=excluded.updated_at""",
            (domain["code"], domain["name"], domain["description"], domain["target_type"],
             domain["event_unit"], domain.get("min_date_precision"), domain["absence_policy"],
             domain.get("source_scope"), domain.get("status", "candidate"), version, now),
        )
    conn.execute("DELETE FROM prediction_event_rules WHERE rule_version = ?", (version,))
    for rule in taxonomy.get("rules", []):
        conn.execute(
            """INSERT INTO prediction_event_rules
               (domain_code, source, fact_type, fact_subtype_regex,
                event_type_regex, event_subtype_regex, polarity,
                evidence_required, description, rule_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rule["domain_code"], rule.get("source"), rule["fact_type"],
             rule.get("fact_subtype_regex"), rule.get("event_type_regex"),
             rule.get("event_subtype_regex"), rule.get("polarity", "positive"),
             int(rule.get("evidence_required", 1)), rule.get("description"), version),
        )


def _iter_facts(conn: sqlite3.Connection) -> Iterable[dict[str, Any]]:
    cursor = conn.execute(
        """SELECT id, person_id, source, fact_type, fact_subtype,
                  date_start, date_end, date_precision, value_text,
                  source_table, source_id, metadata_json
           FROM biography_facts ORDER BY id"""
    )
    for row in cursor:
        yield dict(row)


def standardize_prediction_domains(
    conn: sqlite3.Connection, taxonomy: dict[str, Any] | None = None
) -> dict[str, int]:
    """生成阳性观察结果；没有证据时不插入 negative。"""
    taxonomy = taxonomy or load_taxonomy()
    version = taxonomy["rule_version"]
    rules_by_domain = _compile_rules(taxonomy)
    _insert_definitions(conn, taxonomy)
    conn.execute("DELETE FROM person_prediction_outcomes WHERE rule_version = ?", (version,))

    aggregate: dict[tuple[int, str], dict[str, Any]] = {}
    matched_counts: dict[str, int] = defaultdict(int)
    for fact in _iter_facts(conn):
        for domain in taxonomy.get("domains", []):
            code = domain["code"]
            if not any(fact_matches_rule(fact, rule) for rule in rules_by_domain.get(code, [])):
                continue
            item = aggregate.setdefault((fact["person_id"], code), {
                "first_date_start": None, "first_date_end": None,
                "date_precision": None, "event_count": 0,
                "evidence_count": 0, "sources": set(), "fact_ids": [],
            })
            item["event_count"] += 1
            item["evidence_count"] += 1
            item["sources"].add(fact["source"])
            if len(item["fact_ids"]) < 100:
                item["fact_ids"].append(fact["id"])
            start = fact.get("date_start")
            if start and (item["first_date_start"] is None or start < item["first_date_start"]):
                item["first_date_start"] = start
                item["first_date_end"] = fact.get("date_end")
                item["date_precision"] = fact.get("date_precision")
            matched_counts[code] += 1

    now = datetime.now(timezone.utc).isoformat()
    for (person_id, domain_code), item in aggregate.items():
        conn.execute(
            """INSERT INTO person_prediction_outcomes
               (person_id, domain_code, outcome_status, first_date_start,
                first_date_end, date_precision, event_count, evidence_count,
                source_count, derived_from_json, rule_version, created_at)
               VALUES (?, ?, 'positive', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (person_id, domain_code, item["first_date_start"], item["first_date_end"],
             item["date_precision"], item["event_count"], item["evidence_count"],
             len(item["sources"]), json.dumps(item["fact_ids"], ensure_ascii=False),
             version, now),
        )
    conn.commit()
    return dict(matched_counts)
