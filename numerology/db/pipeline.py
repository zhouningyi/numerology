"""Data pipeline: collect -> calculate bazi -> store in SQLite."""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Optional

from ..collectors.adb import AdbEvent, AdbPerson, _parse_coord
from ..collectors.wikidata import WikidataPerson
from ..engines.bazi import calculate_bazi

logger = logging.getLogger(__name__)


def _parse_adb_date(sbdate: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Parse ADB date format 'YYYY/MM/DD' -> (year, month, day)."""
    parts = sbdate.split("/")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else None
        day = int(parts[2]) if len(parts) > 2 else None
        return year, month, day
    except (ValueError, IndexError):
        return None, None, None


def _parse_adb_time(sbtime: str | None) -> tuple[Optional[int], Optional[int]]:
    """Parse ADB time format 'HH:MM' -> (hour, minute)."""
    if not sbtime:
        return None, None
    m = re.match(r"(\d{1,2}):(\d{2})", sbtime)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _classify_category(cat: str) -> str:
    """Classify an ADB category into type."""
    cat_lower = cat.lower()
    if "occupation" in cat_lower or "vocation" in cat_lower:
        return "occupation"
    elif "event" in cat_lower:
        return "event"
    else:
        return "other"


def insert_adb_person(conn: sqlite3.Connection, person: AdbPerson) -> Optional[int]:
    """Insert an ADB person and compute bazi. Returns person_id or None."""
    year, month, day = _parse_adb_date(person.birth_date)
    if year is None or month is None or day is None:
        return None
    if year < 1 or month < 1 or day < 1:
        return None

    hour, minute = _parse_adb_time(person.birth_time)
    lat = _parse_coord(person.latitude) if person.latitude else None
    lon = _parse_coord(person.longitude) if person.longitude else None

    iso_date = f"{year:04d}-{month:02d}-{day:02d}"
    gender_code = person.gender if person.gender in ("M", "F") else None

    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO persons
               (source, source_id, name, gender, birth_date, birth_time,
                birth_year, birth_month, birth_day, birth_hour, birth_minute,
                birth_place, birth_country, birth_lat, birth_lon,
                rodden_rating, biography)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "adb",
                str(person.page_id),
                person.name,
                gender_code,
                iso_date,
                person.birth_time,
                year,
                month,
                day,
                hour,
                minute,
                person.birth_place,
                person.birth_country,
                lat,
                lon,
                person.rodden_rating,
                person.biography,
            ),
        )
        if cur.rowcount == 0:
            # Already exists
            row = conn.execute(
                "SELECT id FROM persons WHERE source='adb' AND source_id=?",
                (str(person.page_id),),
            ).fetchone()
            return row["id"] if row else None

        person_id = cur.lastrowid

        # Insert categories
        for cat in person.categories:
            conn.execute(
                "INSERT OR IGNORE INTO categories (person_id, category, cat_type) VALUES (?, ?, ?)",
                (person_id, cat, _classify_category(cat)),
            )

        # Insert events
        if person.events:
            _insert_events(conn, person_id, person.events)

        # Calculate and store bazi
        _insert_bazi(
            conn,
            person_id,
            year,
            month,
            day,
            hour,
            minute,
            1 if gender_code == "M" else 0,
        )

        return person_id

    except Exception as e:
        logger.error(f"Error inserting ADB person {person.name}: {e}")
        return None


def insert_wikidata_person(
    conn: sqlite3.Connection, person: WikidataPerson
) -> Optional[int]:
    """Insert a Wikidata person and compute bazi. Returns person_id or None."""
    if person.birth_year is None or person.birth_year < 1:
        return None

    iso_date = person.birth_date

    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO persons
               (source, source_id, name, birth_date,
                birth_year, birth_month, birth_day,
                birth_place, death_date, death_year, cause_of_death,
                date_precision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "wikidata",
                person.qid,
                person.name,
                iso_date,
                person.birth_year,
                person.birth_month,
                person.birth_day,
                person.birth_place,
                person.death_date,
                int(person.death_date.split("-")[0]) if person.death_date else None,
                person.cause_of_death,
                11,
            ),
        )
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT id FROM persons WHERE source='wikidata' AND source_id=?",
                (person.qid,),
            ).fetchone()
            return row["id"] if row else None

        person_id = cur.lastrowid

        # Insert occupations as categories
        for occ in person.occupations:
            conn.execute(
                "INSERT OR IGNORE INTO categories (person_id, category, cat_type) VALUES (?, ?, ?)",
                (person_id, occ, "occupation"),
            )

        # Calculate bazi (三柱 only, no hour)
        if person.birth_month and person.birth_day:
            _insert_bazi(
                conn, person_id, person.birth_year, person.birth_month, person.birth_day
            )

        return person_id

    except Exception as e:
        logger.error(f"Error inserting Wikidata person {person.name}: {e}")
        return None


def _insert_bazi(
    conn: sqlite3.Connection,
    person_id: int,
    year: int,
    month: int,
    day: int,
    hour: int | None = None,
    minute: int = 0,
    gender: int = 1,
):
    """Calculate and insert bazi for a person."""
    try:
        result = calculate_bazi(year, month, day, hour, minute, gender)
    except Exception as e:
        logger.debug(f"BaZi calculation failed for person {person_id}: {e}")
        return

    conn.execute(
        """INSERT OR IGNORE INTO bazi
           (person_id, year_pillar, month_pillar, day_pillar, time_pillar,
            day_master, day_master_element, day_master_yinyang,
            year_shishen_gan, month_shishen_gan, time_shishen_gan,
            wood_count, fire_count, earth_count, metal_count, water_count,
            year_nayin, month_nayin, day_nayin, time_nayin,
            dayun_start_age, has_time_pillar)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            person_id,
            result.year_pillar,
            result.month_pillar,
            result.day_pillar,
            result.time_pillar,
            result.day_master,
            result.day_master_element,
            result.day_master_yinyang,
            result.year_shishen_gan,
            result.month_shishen_gan,
            result.time_shishen_gan,
            result.wood_count,
            result.fire_count,
            result.earth_count,
            result.metal_count,
            result.water_count,
            result.year_nayin,
            result.month_nayin,
            result.day_nayin,
            result.time_nayin,
            result.dayun_start_age,
            1 if result.has_time_pillar else 0,
        ),
    )

    # Insert dayun
    for dy in result.dayun_list:
        conn.execute(
            """INSERT OR IGNORE INTO dayun
               (person_id, start_age, end_age, ganzhi, gan_element, zhi_element)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                person_id,
                dy.start_age,
                dy.end_age,
                dy.ganzhi,
                dy.gan_element,
                dy.zhi_element,
            ),
        )


def _format_event_date(adb_date: str) -> str:
    """将 ADB 日期 'YYYY/MM/DD' 转为 'YYYY-MM-DD'，保留 00。"""
    return adb_date.replace("/", "-") if adb_date else ""


def _insert_events(conn: sqlite3.Connection, person_id: int, events: list[AdbEvent]):
    """将事件列表写入 events 表。"""
    for ev in events:
        conn.execute(
            """INSERT OR IGNORE INTO events
               (person_id, event_code, event_date, event_time, event_notes, event_place)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                person_id,
                ev.event_code,
                _format_event_date(ev.event_date),
                ev.event_time,
                ev.event_notes,
                ev.event_place,
            ),
        )
