"""事件日期标准化测试。"""

from numerology.analysis.event_normalization import (
    normalize_event_date,
    split_event_code,
)


def test_year_only_event_becomes_full_year_interval():
    result = normalize_event_date("1992-00-00")
    assert (result.start, result.end, result.precision) == (
        "1992-01-01",
        "1992-12-31",
        9,
    )
    assert result.status == "warning"


def test_month_only_event_becomes_month_interval():
    result = normalize_event_date("1992-02-00")
    assert (result.start, result.end, result.precision) == (
        "1992-02-01",
        "1992-02-29",
        10,
    )


def test_invalid_calendar_date_is_invalid():
    result = normalize_event_date("1992-02-31")
    assert result.status == "invalid"
    assert "invalid_calendar_date" in result.flags


def test_event_code_split():
    assert split_event_code("Relationship : Marriage") == (
        "Relationship",
        "Marriage",
    )
    assert split_event_code("Death") == ("Death", None)
