# Numerology - 命理学统计研究

## 项目目标
通过大规模统计方法研究中国传统命理学（八字/四柱）的预测有效性。

## 技术栈
- **语言**: Python 3.10+
- **八字计算**: `lunar_python` (pip install lunar_python)
- **数据库**: SQLite (data/numerology.db)
- **数据源**: Astro-Databank (MediaWiki API), Wikidata (SPARQL)

## 项目结构
```
numerology/
├── numerology/              # 核心包
│   ├── collectors/          # 数据采集器
│   │   ├── adb.py          # Astro-Databank 采集器
│   │   └── wikidata.py     # Wikidata SPARQL 采集器
│   ├── engines/            # 计算引擎
│   │   └── bazi.py         # 八字计算 (基于 lunar_python)
│   ├── db/                 # 数据库
│   │   ├── schema.py       # SQLite schema + 连接管理
│   │   └── pipeline.py     # 采集→计算→入库 pipeline
│   └── analysis/           # 统计分析 (待开发)
├── tests/                  # 测试
├── data/                   # 数据文件 (git ignored)
├── collect_adb.py          # ADB 采集脚本
├── collect_wikidata.py     # Wikidata 采集脚本
└── requirements.txt
```

## 数据源说明
- **Astro-Databank**: ~72K 条目，含精确出生时间(分钟级) + Rodden可靠性评级。版权归 Astrodienst AG。
- **Wikidata**: ~487万条日级精度出生日期，无出生时间。CC0 公共领域。

## 使用方法
```bash
pip install -r requirements.txt
python collect_adb.py --limit 1000     # 采集 ADB 数据
python collect_wikidata.py --start-year 1900 --end-year 1950  # 采集 Wikidata
```

## 代码规范
- 全部使用中文注释和文档
- 遵守 Astro-Databank robots.txt (Crawl-delay: 2.0)
- 数据文件不入 git
