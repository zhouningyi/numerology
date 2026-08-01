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
    entry_type      TEXT DEFAULT 'person',  -- 'person', 'event', 'other'
    name            TEXT,                   -- 原始全名（ADB: "Last, First"）
    last_name       TEXT,                   -- 姓
    first_name      TEXT,                   -- 名
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
    birth_lat_raw   TEXT,                  -- ADB 原始纬度字符串
    birth_lon_raw   TEXT,                  -- ADB 原始经度字符串
    tz_meridian     TEXT,                  -- ADB 原始时区中央经线
    tz_abbr         TEXT,                  -- ADB 时区缩写
    time_type       TEXT,                  -- ADB 时间类型
    time_accuracy   TEXT,                  -- ADB 出生时间精度
    time_unknown    INTEGER DEFAULT 0,     -- ADB 是否明确标记时间未知
    sun_degmin      TEXT,                  -- ADB 西洋占星太阳位置
    moon_degmin     TEXT,                  -- ADB 西洋占星月亮位置
    asc_degmin      TEXT,                  -- ADB 西洋占星上升位置
    -- 数据质量
    rodden_rating   TEXT,                  -- AA/A/B/C/DD/X/XX (ADB only)
    date_precision  INTEGER,               -- Wikidata precision: 9=year,10=month,11=day
    -- 生平信息
    death_date      TEXT,                  -- ISO format or NULL
    death_year      INTEGER,
    cause_of_death  TEXT,
    -- 传记/生平
    biography       TEXT,                  -- 传记原文 (ADB Biography section, English)
    biography_zh    TEXT,                  -- 传记中文翻译 (GPT-4o-mini)
    -- 元数据
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);

-- 上游数据快照表：记录下载版本、许可证和校验值
CREATE TABLE IF NOT EXISTS source_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    release_name    TEXT NOT NULL,
    retrieved_at    TEXT NOT NULL,
    source_url      TEXT,
    license         TEXT,
    raw_path        TEXT,
    sha256          TEXT,
    record_count    INTEGER,
    metadata_json   TEXT,
    UNIQUE(source, release_name)
);

-- 来源记录映射表：保留上游 ID 与规范化人物 ID 的关系
CREATE TABLE IF NOT EXISTS source_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES source_snapshots(id),
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    person_id       INTEGER REFERENCES persons(id),
    source_table    TEXT,
    raw_key         TEXT,
    UNIQUE(snapshot_id, source, source_id)
);

-- 出生事实表：保留日期精度和原始字段，避免把模糊年份伪装成具体日期
CREATE TABLE IF NOT EXISTS birth_facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id       INTEGER NOT NULL REFERENCES persons(id),
    source_record_id INTEGER NOT NULL REFERENCES source_records(id),
    calendar        TEXT NOT NULL,
    date_start      TEXT,
    date_end        TEXT,
    date_precision  INTEGER,
    raw_year        INTEGER,
    raw_month       INTEGER,
    raw_day         INTEGER,
    raw_range_code  INTEGER,
    UNIQUE(source_record_id)
);

-- 死亡事实表：与出生事实一样保留来源和日期不确定性
CREATE TABLE IF NOT EXISTS death_facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id       INTEGER NOT NULL REFERENCES persons(id),
    source_record_id INTEGER NOT NULL REFERENCES source_records(id),
    calendar        TEXT NOT NULL,
    date_start      TEXT,
    date_end        TEXT,
    date_precision  INTEGER,
    raw_year        INTEGER,
    raw_month       INTEGER,
    raw_day         INTEGER,
    raw_range_code  INTEGER,
    death_age       INTEGER,
    UNIQUE(source_record_id)
);

-- 质量审计运行表：每次审计保留独立版本
CREATE TABLE IF NOT EXISTS quality_audit_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_version    TEXT NOT NULL,
    checked_at      TEXT NOT NULL,
    current_year    INTEGER NOT NULL,
    flag_count      INTEGER DEFAULT 0
);

-- 逐实体质量标签：不删除原始数据，只记录问题及审计版本
CREATE TABLE IF NOT EXISTS data_quality_flags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_run_id    INTEGER NOT NULL REFERENCES quality_audit_runs(id),
    entity_type     TEXT NOT NULL,          -- person / event / source_record / birth_fact
    entity_id       INTEGER NOT NULL,
    source          TEXT,
    flag_code       TEXT NOT NULL,
    severity        TEXT NOT NULL,          -- error / warning / info
    details_json    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(audit_run_id, entity_type, entity_id, flag_code)
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

-- 生平事件表
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   INTEGER NOT NULL REFERENCES persons(id),
    event_code  TEXT NOT NULL,              -- 事件类型代码，如 'Relationship : Marriage'
    event_date  TEXT,                       -- YYYY-MM-DD（日/月可能为00表示未知）
    event_time  TEXT,                       -- HH:MM or NULL
    event_notes TEXT,                       -- 事件描述
    event_place TEXT,                       -- 事件发生地
    UNIQUE(person_id, event_code, event_date, event_notes)
);

-- 标准化事件表：将 YYYY-00-00 等部分日期转换为日期区间
CREATE TABLE IF NOT EXISTS events_normalized (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL UNIQUE REFERENCES events(id),
    person_id       INTEGER NOT NULL REFERENCES persons(id),
    source          TEXT NOT NULL,
    event_type      TEXT,
    event_subtype   TEXT,
    date_start      TEXT,
    date_end        TEXT,
    date_precision  INTEGER,
    event_time      TEXT,
    event_notes     TEXT,
    event_place     TEXT,
    quality_status  TEXT NOT NULL DEFAULT 'valid',
    quality_flags   TEXT
);

-- 统一生平事实表：事件和分类先以确定性方式导入，传记模型抽取后复用同一结构
CREATE TABLE IF NOT EXISTS biography_facts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id           INTEGER NOT NULL REFERENCES persons(id),
    source              TEXT NOT NULL,
    fact_type           TEXT NOT NULL,       -- event / category / biography / relation
    fact_subtype        TEXT,
    date_start          TEXT,
    date_end            TEXT,
    date_precision      INTEGER,
    value_text          TEXT NOT NULL,
    place               TEXT,
    evidence_text       TEXT,
    source_table        TEXT NOT NULL,       -- events_normalized / categories / biography
    source_id           TEXT NOT NULL,
    extraction_method   TEXT NOT NULL,       -- structured_event / structured_category / local_llm / human
    extractor_version   TEXT NOT NULL,
    confidence          REAL,
    review_status       TEXT NOT NULL DEFAULT 'pending', -- pending / accepted / rejected
    metadata_json       TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(person_id, source_table, source_id, fact_type, fact_subtype, value_text)
);

-- 项目统一分析质量层：不替代 ADB Rodden 等来源原生评级
CREATE TABLE IF NOT EXISTS person_quality_profiles (
    person_id             INTEGER PRIMARY KEY REFERENCES persons(id),
    source                TEXT NOT NULL,
    native_rating         TEXT,
    native_quality_json   TEXT,
    date_quality          TEXT NOT NULL,    -- day / month / year / unknown
    time_quality          TEXT NOT NULL,    -- minute / unknown
    analysis_tier         TEXT NOT NULL,    -- full_bazi / three_pillars / date_interval / unusable
    quality_flags         TEXT,
    rule_version          TEXT NOT NULL,
    generated_at          TEXT NOT NULL
);

-- 预测域定义：用于把命理研究的“预测对象”固定为可重复统计的终点
CREATE TABLE IF NOT EXISTS prediction_domains (
    code                TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL,
    target_type         TEXT NOT NULL,   -- binary / time_to_event / count / recurrent
    event_unit          TEXT NOT NULL,   -- person / event
    min_date_precision  INTEGER,
    absence_policy      TEXT NOT NULL,   -- unknown / censored / not_applicable
    source_scope        TEXT,
    status              TEXT NOT NULL DEFAULT 'candidate',
    rule_version        TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- 预测域匹配规则：规则是数据而不是散落在分析代码中的 if/else
CREATE TABLE IF NOT EXISTS prediction_event_rules (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_code             TEXT NOT NULL REFERENCES prediction_domains(code),
    source                  TEXT,
    fact_type               TEXT NOT NULL,
    fact_subtype_regex      TEXT,
    event_type_regex        TEXT,
    event_subtype_regex     TEXT,
    polarity                TEXT NOT NULL DEFAULT 'positive',
    evidence_required       INTEGER NOT NULL DEFAULT 1,
    description             TEXT,
    rule_version            TEXT NOT NULL,
    UNIQUE(domain_code, source, fact_type, fact_subtype_regex,
           event_type_regex, event_subtype_regex, polarity, rule_version)
);

-- 标准化观察结果：只记录被观察到的阳性证据；没有记录不等于阴性
CREATE TABLE IF NOT EXISTS person_prediction_outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id           INTEGER NOT NULL REFERENCES persons(id),
    domain_code         TEXT NOT NULL REFERENCES prediction_domains(code),
    outcome_status      TEXT NOT NULL,   -- positive / unknown / censored
    first_date_start    TEXT,
    first_date_end      TEXT,
    date_precision      INTEGER,
    event_count         INTEGER NOT NULL DEFAULT 0,
    evidence_count      INTEGER NOT NULL DEFAULT 0,
    source_count        INTEGER NOT NULL DEFAULT 0,
    derived_from_json   TEXT,
    rule_version        TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(person_id, domain_code, rule_version)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_events_person ON events(person_id);
CREATE INDEX IF NOT EXISTS idx_events_code ON events(event_code);
CREATE INDEX IF NOT EXISTS idx_events_normalized_person ON events_normalized(person_id);
CREATE INDEX IF NOT EXISTS idx_events_normalized_type ON events_normalized(event_type, event_subtype);
CREATE INDEX IF NOT EXISTS idx_biography_facts_person ON biography_facts(person_id);
CREATE INDEX IF NOT EXISTS idx_biography_facts_type ON biography_facts(fact_type, fact_subtype);
CREATE INDEX IF NOT EXISTS idx_biography_facts_date ON biography_facts(date_start, date_end);
CREATE INDEX IF NOT EXISTS idx_biography_facts_review ON biography_facts(review_status);
CREATE INDEX IF NOT EXISTS idx_quality_profiles_source ON person_quality_profiles(source);
CREATE INDEX IF NOT EXISTS idx_quality_profiles_tier ON person_quality_profiles(analysis_tier);
CREATE INDEX IF NOT EXISTS idx_persons_source ON persons(source);
CREATE INDEX IF NOT EXISTS idx_persons_entry_type ON persons(entry_type);
CREATE INDEX IF NOT EXISTS idx_persons_birth_year ON persons(birth_year);
CREATE INDEX IF NOT EXISTS idx_persons_rodden ON persons(rodden_rating);
CREATE INDEX IF NOT EXISTS idx_persons_gender ON persons(gender);
CREATE INDEX IF NOT EXISTS idx_source_snapshots_source ON source_snapshots(source);
CREATE INDEX IF NOT EXISTS idx_source_records_person ON source_records(person_id);
CREATE INDEX IF NOT EXISTS idx_source_records_source ON source_records(source, source_id);
CREATE INDEX IF NOT EXISTS idx_birth_facts_person ON birth_facts(person_id);
CREATE INDEX IF NOT EXISTS idx_death_facts_person ON death_facts(person_id);
CREATE INDEX IF NOT EXISTS idx_quality_runs_checked_at ON quality_audit_runs(checked_at);
CREATE INDEX IF NOT EXISTS idx_quality_flags_entity ON data_quality_flags(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_quality_flags_code ON data_quality_flags(flag_code, severity);
CREATE INDEX IF NOT EXISTS idx_categories_person ON categories(person_id);
CREATE INDEX IF NOT EXISTS idx_categories_type ON categories(cat_type);
CREATE INDEX IF NOT EXISTS idx_categories_category ON categories(category);
CREATE INDEX IF NOT EXISTS idx_bazi_person ON bazi(person_id);
CREATE INDEX IF NOT EXISTS idx_bazi_day_master ON bazi(day_master);
CREATE INDEX IF NOT EXISTS idx_bazi_day_element ON bazi(day_master_element);
CREATE INDEX IF NOT EXISTS idx_dayun_person ON dayun(person_id);
CREATE INDEX IF NOT EXISTS idx_prediction_rules_domain ON prediction_event_rules(domain_code);
CREATE INDEX IF NOT EXISTS idx_prediction_outcomes_domain ON person_prediction_outcomes(domain_code, outcome_status);
CREATE INDEX IF NOT EXISTS idx_prediction_outcomes_person ON person_prediction_outcomes(person_id);
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
    # 现有数据库由早期版本创建时没有这些列，使用显式迁移保证可重复初始化。
    columns = {
        "birth_lat_raw": "TEXT",
        "birth_lon_raw": "TEXT",
        "tz_meridian": "TEXT",
        "tz_abbr": "TEXT",
        "time_type": "TEXT",
        "time_accuracy": "TEXT",
        "time_unknown": "INTEGER DEFAULT 0",
        "sun_degmin": "TEXT",
        "moon_degmin": "TEXT",
        "asc_degmin": "TEXT",
    }
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(persons)").fetchall()
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE persons ADD COLUMN {name} {definition}")
    conn.commit()
    return conn
