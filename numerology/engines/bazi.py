"""BaZi (Four Pillars of Destiny) calculation engine.

Wraps lunar_python to provide a clean batch-processing API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lunar_python import Solar


# 五行映射
ELEMENT_MAP = {
    "甲": "木",
    "乙": "木",
    "丙": "火",
    "丁": "火",
    "戊": "土",
    "己": "土",
    "庚": "金",
    "辛": "金",
    "壬": "水",
    "癸": "水",
}

YINYANG_MAP = {
    "甲": "阳",
    "乙": "阴",
    "丙": "阳",
    "丁": "阴",
    "戊": "阳",
    "己": "阴",
    "庚": "阳",
    "辛": "阴",
    "壬": "阳",
    "癸": "阴",
}

# 地支藏干
BRANCH_HIDDEN_STEMS = {
    "子": ["癸"],
    "丑": ["己", "癸", "辛"],
    "寅": ["甲", "丙", "戊"],
    "卯": ["乙"],
    "辰": ["戊", "乙", "癸"],
    "巳": ["丙", "庚", "戊"],
    "午": ["丁", "己"],
    "未": ["己", "丁", "乙"],
    "申": ["庚", "壬", "戊"],
    "酉": ["辛"],
    "戌": ["戊", "辛", "丁"],
    "亥": ["壬", "甲"],
}


@dataclass
class DaYunInfo:
    """大运信息。"""

    start_age: int
    end_age: int
    ganzhi: str
    gan_element: str
    zhi_element: str


@dataclass
class BaZiResult:
    """八字计算结果。"""

    # 四柱
    year_pillar: str
    month_pillar: str
    day_pillar: str
    time_pillar: Optional[str]  # None if birth hour unknown

    # 日主
    day_master: str
    day_master_element: str
    day_master_yinyang: str

    # 十神 (天干)
    year_shishen_gan: str
    month_shishen_gan: str
    time_shishen_gan: Optional[str]

    # 五行统计 (天干+地支藏干)
    wood_count: int = 0
    fire_count: int = 0
    earth_count: int = 0
    metal_count: int = 0
    water_count: int = 0

    # 纳音
    year_nayin: str = ""
    month_nayin: str = ""
    day_nayin: str = ""
    time_nayin: Optional[str] = None

    # 大运
    dayun_start_age: Optional[int] = None
    dayun_list: list[DaYunInfo] = field(default_factory=list)

    # 是否有时柱
    has_time_pillar: bool = False


def _count_elements(pillars: list[str]) -> dict[str, int]:
    """统计四柱中的五行数量 (天干 + 地支藏干)。"""
    counts = {"木": 0, "火": 0, "土": 0, "金": 0, "水": 0}
    for pillar in pillars:
        if not pillar:
            continue
        gan = pillar[0]
        zhi = pillar[1]
        # 天干
        elem = ELEMENT_MAP.get(gan)
        if elem:
            counts[elem] += 1
        # 地支藏干
        for hidden in BRANCH_HIDDEN_STEMS.get(zhi, []):
            elem = ELEMENT_MAP.get(hidden)
            if elem:
                counts[elem] += 1
    return counts


def calculate_bazi(
    year: int,
    month: int,
    day: int,
    hour: Optional[int] = None,
    minute: int = 0,
    gender: int = 1,  # 1=male, 0=female
) -> BaZiResult:
    """计算八字。

    Args:
        year: 公历年
        month: 公历月
        day: 公历日
        hour: 公历时 (0-23), None表示不知道出生时间
        minute: 公历分 (0-59)
        gender: 性别, 1=男, 0=女

    Returns:
        BaZiResult 包含完整的八字信息
    """
    # 如果没有出生时间，默认用正午12点算三柱
    # (时柱将标记为None)
    h = hour if hour is not None else 12
    m = minute

    solar = Solar(year, month, day, h, m, 0)
    lunar = solar.getLunar()
    eight_char = lunar.getEightChar()

    year_p = eight_char.getYear()
    month_p = eight_char.getMonth()
    day_p = eight_char.getDay()
    time_p = eight_char.getTime() if hour is not None else None

    day_master = day_p[0]
    day_master_elem = ELEMENT_MAP.get(day_master, "")
    day_master_yy = YINYANG_MAP.get(day_master, "")

    # 十神
    year_ss = eight_char.getYearShiShenGan()
    month_ss = eight_char.getMonthShiShenGan()
    time_ss = eight_char.getTimeShiShenGan() if hour is not None else None

    # 五行统计
    pillars = [year_p, month_p, day_p]
    if time_p:
        pillars.append(time_p)
    elem_counts = _count_elements(pillars)

    # 纳音
    year_ny = eight_char.getYearNaYin()
    month_ny = eight_char.getMonthNaYin()
    day_ny = eight_char.getDayNaYin()
    time_ny = eight_char.getTimeNaYin() if hour is not None else None

    # 大运
    dayun_start_age = None
    dayun_list = []
    try:
        yun = eight_char.getYun(gender)
        dayun_start_age = yun.getStartYear()
        for dy in yun.getDaYun():
            age = dy.getStartAge()
            if age <= 0:
                continue
            gz = dy.getGanZhi()
            gan_elem = ELEMENT_MAP.get(gz[0], "") if gz else ""
            zhi_elem = (
                ELEMENT_MAP.get(BRANCH_HIDDEN_STEMS.get(gz[1], [""])[0], "")
                if gz and len(gz) > 1
                else ""
            )
            dayun_list.append(
                DaYunInfo(
                    start_age=age,
                    end_age=age + 9,
                    ganzhi=gz,
                    gan_element=gan_elem,
                    zhi_element=zhi_elem,
                )
            )
    except Exception:
        pass

    return BaZiResult(
        year_pillar=year_p,
        month_pillar=month_p,
        day_pillar=day_p,
        time_pillar=time_p,
        day_master=day_master,
        day_master_element=day_master_elem,
        day_master_yinyang=day_master_yy,
        year_shishen_gan=year_ss,
        month_shishen_gan=month_ss,
        time_shishen_gan=time_ss,
        wood_count=elem_counts["木"],
        fire_count=elem_counts["火"],
        earth_count=elem_counts["土"],
        metal_count=elem_counts["金"],
        water_count=elem_counts["水"],
        year_nayin=year_ny,
        month_nayin=month_ny,
        day_nayin=day_ny,
        time_nayin=time_ny,
        dayun_start_age=dayun_start_age,
        dayun_list=dayun_list,
        has_time_pillar=hour is not None,
    )
