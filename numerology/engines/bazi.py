"""BaZi (Four Pillars of Destiny) calculation engine.

Wraps lunar_python to provide a clean batch-processing API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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

# 地支藏干（列表顺序即 本气 → 中气 → 余气）
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

# 藏干权重方案：按 [本气, 中气, 余气] 顺序取权重
# - equal:   全部等权，与早期实现一致，保留作为对照基线
# - classic: 本气为主、中余气递减，古籍通行的简化比例
CANGGAN_WEIGHT_SCHEMES: dict[str, list[float]] = {
    "equal": [1.0, 1.0, 1.0],
    "classic": [1.0, 0.5, 0.2],
}

# 天干在五行统计中的权重（天干透出，恒为 1）
STEM_WEIGHT = 1.0


def equation_of_time(dt: datetime) -> float:
    """均时差（分钟）：真太阳时与平太阳时之差。

    使用通行的近似公式，精度约 ±30 秒 —— 相对于时柱 2 小时的跨度可忽略。
    """
    n = dt.timetuple().tm_yday
    b = 2 * math.pi * (n - 81) / 364.0
    return 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)


def solar_time_correction(dt: datetime, lon: float, meridian_lon: float) -> float:
    """真太阳时校正量（分钟）= 经度差校正 + 均时差。

    Args:
        dt: 当地钟表时间
        lon: 出生地经度（东经为正）
        meridian_lon: 记录时间所依据的时区中央经线

    地球每 15° 经度对应 1 小时，即每 1° 对应 4 分钟。
    """
    return (lon - meridian_lon) * 4.0 + equation_of_time(dt)


def to_true_solar_time(
    dt: datetime, lon: float, meridian_lon: float
) -> tuple[datetime, float]:
    """将当地钟表时间换算为真太阳时。

    Returns:
        (校正后的时间, 校正量分钟)。校正可能跨日，从而改变日柱。
    """
    correction = solar_time_correction(dt, lon, meridian_lon)
    return dt + timedelta(minutes=correction), correction


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

    # 五行统计 (天干+地支藏干)，equal 方案下为整数值
    wood_count: float = 0.0
    fire_count: float = 0.0
    earth_count: float = 0.0
    metal_count: float = 0.0
    water_count: float = 0.0

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

    # 排盘配置与校正记录（用于流派对比和结果复现）
    sect: int = 2  # 1=晚子时归次日, 2=晚子时归当日
    canggan_weights: str = "equal"
    solar_correction_min: Optional[float] = None  # 真太阳时校正量（分钟）
    corrected_datetime: Optional[str] = None  # 校正后的时刻，跨日时与原始日期不同


def _count_elements(
    pillars: list[str], weights: str = "equal"
) -> dict[str, float]:
    """统计四柱中的五行力量 (天干 + 地支藏干)。

    Args:
        pillars: 干支柱列表
        weights: 藏干权重方案名，见 CANGGAN_WEIGHT_SCHEMES

    equal 方案下每个藏干等权计 1，结果与整数计数一致。
    """
    scheme = CANGGAN_WEIGHT_SCHEMES.get(weights)
    if scheme is None:
        raise ValueError(
            f"未知的藏干权重方案 {weights!r}，"
            f"可选：{sorted(CANGGAN_WEIGHT_SCHEMES)}"
        )

    counts = {"木": 0.0, "火": 0.0, "土": 0.0, "金": 0.0, "水": 0.0}
    for pillar in pillars:
        if not pillar:
            continue
        gan = pillar[0]
        zhi = pillar[1]
        # 天干
        elem = ELEMENT_MAP.get(gan)
        if elem:
            counts[elem] += STEM_WEIGHT
        # 地支藏干：按 本气/中气/余气 的位置取权重
        for idx, hidden in enumerate(BRANCH_HIDDEN_STEMS.get(zhi, [])):
            elem = ELEMENT_MAP.get(hidden)
            if elem:
                counts[elem] += scheme[min(idx, len(scheme) - 1)]
    return {k: round(v, 4) for k, v in counts.items()}


def calculate_bazi(
    year: int,
    month: int,
    day: int,
    hour: Optional[int] = None,
    minute: int = 0,
    gender: int = 1,  # 1=male, 0=female
    *,
    lon: Optional[float] = None,
    tz_meridian_lon: Optional[float] = None,
    true_solar_time: bool = False,
    sect: int = 2,
    canggan_weights: str = "equal",
) -> BaZiResult:
    """计算八字。

    Args:
        year: 公历年
        month: 公历月
        day: 公历日
        hour: 公历时 (0-23), None表示不知道出生时间
        minute: 公历分 (0-59)
        gender: 性别, 1=男, 0=女
        lon: 出生地经度（东经为正），真太阳时校正所需
        tz_meridian_lon: 记录时间所依据的时区中央经线
        true_solar_time: 是否换算为真太阳时。需同时提供 lon 与 tz_meridian_lon
        sect: 子时流派。1=晚子时(23:00-24:00)归次日，2=归当日。
            影响日柱即日主，是各家分歧最大的排盘参数之一
        canggan_weights: 藏干权重方案，见 CANGGAN_WEIGHT_SCHEMES

    默认参数保持与早期实现一致的行为（不校正真太阳时、sect=2、藏干等权），
    以便同一批数据可做修正前后的对照。

    Returns:
        BaZiResult 包含完整的八字信息
    """
    # 如果没有出生时间，默认用正午12点算三柱
    # (时柱将标记为None)
    h = hour if hour is not None else 12
    m = minute

    # 真太阳时校正：可能跨日，进而改变日柱
    correction_min = None
    corrected_str = None
    if true_solar_time and lon is not None and tz_meridian_lon is not None:
        corrected, correction_min = to_true_solar_time(
            datetime(year, month, day, h, m), lon, tz_meridian_lon
        )
        corrected_str = corrected.strftime("%Y-%m-%d %H:%M")
        year, month, day = corrected.year, corrected.month, corrected.day
        h, m = corrected.hour, corrected.minute
        correction_min = round(correction_min, 2)

    solar = Solar(year, month, day, h, m, 0)
    lunar = solar.getLunar()
    eight_char = lunar.getEightChar()
    eight_char.setSect(sect)

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
    elem_counts = _count_elements(pillars, weights=canggan_weights)

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
        sect=sect,
        canggan_weights=canggan_weights,
        solar_correction_min=correction_min,
        corrected_datetime=corrected_str,
    )
