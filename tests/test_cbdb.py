"""CBDB 日期和精度转换测试。"""

from numerology.collectors.cbdb import date_interval, date_precision, partial_date


def test_year_precision_is_interval():
    assert date_precision(1101, None, None) == 9
    assert date_interval(1101, None, None) == ("1101-01-01", "1101-12-31")
    assert partial_date(1101, None, None) == "1101"


def test_month_precision_does_not_invent_day():
    assert date_precision(1101, 5, None) == 10
    assert date_interval(1101, 5, None) == ("1101-05-01", "1101-05-31")
    assert partial_date(1101, 5, None) == "1101-05"


def test_day_precision_is_exact():
    assert date_precision(1101, 5, 10) == 11
    assert date_interval(1101, 5, 10) == ("1101-05-10", "1101-05-10")
    assert partial_date(1101, 5, 10) == "1101-05-10"


def test_unknown_or_historical_year_does_not_make_iso_interval():
    assert date_precision(None, None, None) is None
    assert date_interval(0, None, None) == (None, None)
    assert date_interval(-551, None, None) == (None, None)
