"""ADB 坐标解析和归一化测试。"""

from numerology.collectors.adb import _normalize_longitude, _parse_coord


def test_degree_minute_second_coordinate():
    assert _parse_coord("112e2830") == 112.475
    assert _parse_coord("71w07") == -71.1167


def test_dateline_longitude_wraps_without_losing_raw_value():
    assert _normalize_longitude(_parse_coord("182w3040")) == 177.4889
