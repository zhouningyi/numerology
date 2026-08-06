# 脚本目录与运行方式

根目录只保留应用入口 `server.py` 和旧命令兼容入口。脚本实现按职责放在本目录：

| 目录 | 用途 |
| --- | --- |
| `scripts/collectors/` | Astro-Databank、Wikidata、NDERF、CBDB 等下载与导入 |
| `scripts/canon/` | 古籍下载、分章、OCR、扫描页、版本对齐、翻译与语料分层 |
| `scripts/nde/` | 濒死体验解析、翻译、标签、证据映射与向量 |
| `scripts/person/` | 人物生平抽取、抽样与传记翻译 |
| `scripts/analysis/` | 事件规范化、预测域和质量画像 |
| `scripts/quality/` | 数据与语料质量检查 |

统一从项目根目录运行，使用 Python 模块入口。例如：

```bash
python3 -m scripts.collectors.collect_adb --limit 1000
python3 -m scripts.canon.ocr_canon --help
python3 -m scripts.canon.translate_huayan_segments --list-missing
python3 -m scripts.canon.download_daodejing
python3 -m scripts.canon.process_daodejing
python3 -m scripts.canon.translate_daodejing --materialize
python3 -m scripts.canon.align_daodejing_sentences
python3 -m scripts.nde.reclassify_nde
python3 -m scripts.quality.audit_data --persist
```

为兼容已有文档、测试和个人命令，根目录仍保留同名薄入口，例如
`python3 collect_adb.py`、`import process_canon_layers` 仍然有效。新代码和新文档优先使用
`python3 -m scripts.<分组>.<脚本>`，避免再把实现放回根目录。

脚本默认以项目根目录为当前工作目录，输入输出仍位于 `data/`；数据文件不进入 git。
