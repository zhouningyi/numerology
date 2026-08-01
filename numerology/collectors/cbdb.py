"""CBDB SQLite 原始库读取器。

本模块只负责读取 CBDB 原始 SQLite，不修改上游数据库结构。
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator, Optional


def _as_int(value: object) -> Optional[int]:
    """将 CBDB 字段安全转换为整数。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_year(value: object) -> Optional[int]:
    """读取年份，CBDB 用 0 表示未知。"""
    result = _as_int(value)
    return None if result == 0 else result


def _as_month_or_day(value: object) -> Optional[int]:
    """读取月/日，CBDB 用 0 表示未知。"""
    result = _as_int(value)
    return None if result == 0 else result


def _positive(value: Optional[int]) -> Optional[int]:
    """仅保留可用于 ISO 日期区间的正数。"""
    return value if value is not None and value >= 1 else None


def date_precision(
    year: Optional[int], month: Optional[int], day: Optional[int]
) -> Optional[int]:
    """按照 Wikidata 兼容的约定返回日期精度：年 9、月 10、日 11。"""
    if year is None:
        return None
    if month is None:
        return 9
    if day is None:
        return 10
    return 11


def partial_date(
    year: Optional[int], month: Optional[int], day: Optional[int]
) -> Optional[str]:
    """生成不补造缺失部分的部分日期字符串。"""
    year = _positive(year)
    if year is None:
        return None
    if month is None:
        return f"{year:04d}"
    if day is None:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}-{month:02d}-{day:02d}"


def date_interval(
    year: Optional[int], month: Optional[int], day: Optional[int]
) -> tuple[Optional[str], Optional[str]]:
    """将部分日期转换为闭区间，不对缺失的日/月伪造精确日期。"""
    year = _positive(year)
    if year is None:
        return None, None
    if month is None:
        return f"{year:04d}-01-01", f"{year:04d}-12-31"
    if month < 1 or month > 12:
        return None, None
    if day is None:
        last_day = calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"
    try:
        date(year, month, day)
    except ValueError:
        return None, None
    value = f"{year:04d}-{month:02d}-{day:02d}"
    return value, value


@dataclass(frozen=True)
class CbdbPerson:
    """CBDB 人物的第一批规范化字段。"""

    source_id: str
    name: Optional[str]
    name_chn: Optional[str]
    last_name: Optional[str]
    first_name: Optional[str]
    gender: Optional[str]
    birth_year: Optional[int]
    birth_month: Optional[int]
    birth_day: Optional[int]
    birth_range: Optional[int]
    death_year: Optional[int]
    death_age: Optional[int]
    index_year: Optional[int]

    @property
    def birth_precision(self) -> Optional[int]:
        return date_precision(self.birth_year, self.birth_month, self.birth_day)

    @property
    def birth_date(self) -> Optional[str]:
        return partial_date(self.birth_year, self.birth_month, self.birth_day)

    @property
    def birth_start(self) -> Optional[str]:
        return date_interval(
            self.birth_year, self.birth_month, self.birth_day
        )[0]

    @property
    def birth_end(self) -> Optional[str]:
        return date_interval(
            self.birth_year, self.birth_month, self.birth_day
        )[1]

    @property
    def death_precision(self) -> Optional[int]:
        return date_precision(self.death_year, None, None)

    @property
    def death_date(self) -> Optional[str]:
        return partial_date(self.death_year, None, None)

    @property
    def death_start(self) -> Optional[str]:
        return date_interval(self.death_year, None, None)[0]

    @property
    def death_end(self) -> Optional[str]:
        return date_interval(self.death_year, None, None)[1]


class CbdbReader:
    """以只读方式遍历 CBDB 的 BIOG_MAIN 表。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        """打开只读连接，避免误修改上游数据库。"""
        uri = f"file:{self.db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def iter_people(self, batch_size: int = 5000) -> Iterator[CbdbPerson]:
        """分批读取人物基本信息。"""
        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0")

        conn = self.connect()
        try:
            cursor = conn.execute(
                """
                SELECT c_personid, c_name, c_name_chn,
                       c_surname, c_surname_chn, c_mingzi, c_mingzi_chn,
                       c_female, c_birthyear, c_by_month, c_by_day,
                       c_by_range, c_deathyear, c_death_age, c_index_year
                FROM BIOG_MAIN
                ORDER BY c_personid
                """
            )
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    female = _as_int(row["c_female"])
                    yield CbdbPerson(
                        source_id=str(row["c_personid"]),
                        name=row["c_name"],
                        name_chn=row["c_name_chn"],
                        last_name=row["c_surname_chn"] or row["c_surname"],
                        first_name=row["c_mingzi_chn"] or row["c_mingzi"],
                        gender=("F" if female == 1 else "M" if female == 0 else None),
                        birth_year=_as_year(row["c_birthyear"]),
                        birth_month=_as_month_or_day(row["c_by_month"]),
                        birth_day=_as_month_or_day(row["c_by_day"]),
                        birth_range=_as_int(row["c_by_range"]),
                        death_year=_as_year(row["c_deathyear"]),
                        death_age=_as_int(row["c_death_age"]),
                        index_year=_as_int(row["c_index_year"]),
                    )
        finally:
            conn.close()
