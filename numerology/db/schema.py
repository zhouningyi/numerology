"""SQLite database schema and connection management."""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "numerology.db"

SCHEMA_SQL = """
-- 人物基础信息表
CREATE TABLE IF NOT EXISTS persons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,          -- 'adb' or 'wikidata'
    source_id       TEXT,                   -- ADB page_id or Wikidata Q-id
    name            TEXT,
    gender          TEXT,                   -- 'M', 'F', or NULL
    -- 出生信息
    birth_date      TEXT,                   -- ISO format: YYYY-MM-DD
    birth_time      TEXT,                   -- HH:MM or NULL
    birth_year      INTEGER,
    birth_month     INTEGER,
    birth_day       INTEGER,
    birth_hour      INTEGER,               -- 0-23, NULL if unknown
    birth_minute    INTEGER,               -- 0-59, NULL if unknown
    birth_place     TEXT,
    birth_country   TEXT,
    birth_lat       REAL,                  -- 十进制纬度
    birth_lon       REAL,                  -- 十进制经度
    -- 数据质量
    rodden_rating   TEXT,                  -- AA/A/B/C/DD/X/XX (ADB only)
    date_precision  INTEGER,               -- Wikidata precision: 9=year,10=month,11=day
    -- 生平信息
    death_date      TEXT,                  -- ISO format or NULL
    death_year      INTEGER,
    cause_of_death  TEXT,
    -- 元数据
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

-- 职业/分类表
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   INTEGER NOT NULL REFERENCES persons(id),
    category    TEXT NOT NULL,              -- 原始分类文本
    cat_type    TEXT,                       -- 'occupation', 'event', 'other'
    UNIQUE(person_id, category)
);

-- 八字计算结果表
CREATE TABLE IF NOT EXISTS bazi (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id       INTEGER NOT NULL UNIQUE REFERENCES persons(id),
    -- 四柱
    year_pillar     TEXT,                  -- 如 '己卯'
    month_pillar    TEXT,
    day_pillar      TEXT,
    time_pillar     TEXT,                  -- NULL if birth_hour unknown
    -- 日主
    day_master      TEXT,                  -- 天干: 甲乙丙丁...
    day_master_element TEXT,               -- 五行: 木火土金水
    day_master_yinyang TEXT,               -- 阴/阳
    -- 十神 (天干)
    year_shishen_gan TEXT,                 -- 年干十神
    month_shishen_gan TEXT,                -- 月干十神
    time_shishen_gan TEXT,                 -- 时干十神 (NULL if no time)
    -- 五行统计
    wood_count      INTEGER DEFAULT 0,
    fire_count      INTEGER DEFAULT 0,
    earth_count     INTEGER DEFAULT 0,
    metal_count     INTEGER DEFAULT 0,
    water_count     INTEGER DEFAULT 0,
    -- 纳音
    year_nayin      TEXT,
    month_nayin     TEXT,
    day_nayin       TEXT,
    time_nayin      TEXT,
    -- 大运起始年龄
    dayun_start_age INTEGER,
    -- 是否有完整四柱 (含时柱)
    has_time_pillar INTEGER DEFAULT 0      -- 0=三柱, 1=四柱
);

-- 大运表
CREATE TABLE IF NOT EXISTS dayun (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   INTEGER NOT NULL REFERENCES persons(id),
    start_age   INTEGER NOT NULL,
    end_age     INTEGER NOT NULL,
    ganzhi      TEXT NOT NULL,             -- 干支
    gan_element TEXT,                      -- 天干五行
    zhi_element TEXT,                      -- 地支五行
    UNIQUE(person_id, start_age)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_persons_source ON persons(source);
CREATE INDEX IF NOT EXISTS idx_persons_birth_year ON persons(birth_year);
CREATE INDEX IF NOT EXISTS idx_persons_rodden ON persons(rodden_rating);
CREATE INDEX IF NOT EXISTS idx_persons_gender ON persons(gender);
CREATE INDEX IF NOT EXISTS idx_categories_person ON categories(person_id);
CREATE INDEX IF NOT EXISTS idx_categories_type ON categories(cat_type);
CREATE INDEX IF NOT EXISTS idx_categories_category ON categories(category);
CREATE INDEX IF NOT EXISTS idx_bazi_person ON bazi(person_id);
CREATE INDEX IF NOT EXISTS idx_bazi_day_master ON bazi(day_master);
CREATE INDEX IF NOT EXISTS idx_bazi_day_element ON bazi(day_master_element);
CREATE INDEX IF NOT EXISTS idx_dayun_person ON dayun(person_id);
"""


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode enabled."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Initialize database with schema."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn
