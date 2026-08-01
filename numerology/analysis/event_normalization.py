"""生平事件日期和类型标准化。"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class NormalizedDate:
    """标准化后的日期区间。"""

    start: Optional[str]
    end: Optional[str]
    precision: Optional[int]
    status: str
    flags: tuple[str, ...]


def normalize_event_date(value: Optional[str]) -> NormalizedDate:
    """将完整或部分日期转换为闭区间。

    精度使用 Wikidata 约定：年 9、月 10、日 11。
    """
    if not value:
        return NormalizedDate(None, None, None, "invalid", ("missing_date",))

    parts = value.strip().split("-")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return NormalizedDate(None, None, None, "invalid", ("malformed_date",))

    year, month, day = (int(part) for part in parts)
    if year < 1 or year > 9999:
        return NormalizedDate(None, None, None, "invalid", ("year_out_of_range",))

    if month == 0 and day == 0:
        return NormalizedDate(
            f"{year:04d}-01-01",
            f"{year:04d}-12-31",
            9,
            "warning",
            ("year_precision",),
        )
    if 1 <= month <= 12 and day == 0:
        last_day = calendar.monthrange(year, month)[1]
        return NormalizedDate(
            f"{year:04d}-{month:02d}-01",
            f"{year:04d}-{month:02d}-{last_day:02d}",
            10,
            "warning",
            ("month_precision",),
        )
    if 1 <= month <= 12 and 1 <= day <= 31:
        try:
            date(year, month, day)
        except ValueError:
            return NormalizedDate(None, None, None, "invalid", ("invalid_calendar_date",))
        exact = f"{year:04d}-{month:02d}-{day:02d}"
        return NormalizedDate(exact, exact, 11, "valid", ())

    return NormalizedDate(None, None, None, "invalid", ("invalid_month_or_day",))


def split_event_code(event_code: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """拆分 ADB 的 ``类型 : 子类型`` 编码。"""
    if not event_code:
        return None, None
    parts = event_code.split(" : ", 1)
    event_type = parts[0].strip() or None
    event_subtype = parts[1].strip() or None if len(parts) > 1 else None
    return event_type, event_subtype


def flags_json(flags: tuple[str, ...]) -> Optional[str]:
    """将质量标记序列编码为 JSON。"""
    return json.dumps(list(flags), ensure_ascii=False) if flags else None
