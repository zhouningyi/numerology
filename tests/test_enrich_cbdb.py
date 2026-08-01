"""CBDB 关联事实转换测试。"""

from enrich_cbdb import year_interval


def test_cbdb_years_become_intervals_without_inventing_dates():
    assert year_interval(1700) == ("1700-01-01", "1700-12-31", 9)
    assert year_interval(1700, 1710) == ("1700-01-01", "1710-12-31", 9)
    assert year_interval(None, None) == (None, None, None)
    assert year_interval(-551, None) == (None, None, None)
