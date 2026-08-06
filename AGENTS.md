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
├── scripts/                # 按职责分层的可执行脚本
│   ├── collectors/         # 数据源下载与导入
│   ├── canon/              # 古籍、OCR、版本对齐与翻译
│   ├── nde/                # 濒死体验资料处理
│   ├── person/             # 人物生平处理
│   ├── analysis/           # 事件、预测域与质量画像
│   └── quality/            # 数据和语料审计
├── tests/                  # 测试
├── data/                   # 数据文件 (git ignored)
├── server.py               # Web 应用入口
└── requirements.txt
```

## 数据源说明
- **Astro-Databank**: ~72K 条目，含精确出生时间(分钟级) + Rodden可靠性评级。版权归 Astrodienst AG。
- **Wikidata**: ~487万条日级精度出生日期，无出生时间。CC0 公共领域。

## 使用方法
```bash
pip install -r requirements.txt
python -m scripts.collectors.collect_adb --limit 1000     # 采集 ADB 数据
python -m scripts.collectors.collect_wikidata --start-year 1900 --end-year 1950  # 采集 Wikidata
```

脚本必须从项目根目录运行。根目录下同名 `.py` 文件目前是兼容旧命令和旧测试的薄入口；新脚本
统一放入 `scripts/`，具体映射见 `scripts/README.md`。

## 代码规范
- 全部使用中文注释和文档
- 遵守 Astro-Databank robots.txt (Crawl-delay: 2.0)
- 数据文件不入 git
