# 古籍 OCR 与规则校勘规范

更新日期：2026-08-01

## 1. 核心原则

OCR 的作用是把扫描页转换成可搜索、可比对的文字。扫描图像仍然是版本核验的第一证据，OCR 文本不能替代扫描页；网页录入、现代点校和白话解释也不能直接当作古籍原文。

因此，规则处理顺序固定为：

```text
扫描 PDF → 页面图像 → OCR 原始结果 → 与网页/录入本比对 → 人工回看扫描页
       → 校订文本 → 现代化解释 → 可计算规则 → 预注册假设
```

## 2. 本项目的 OCR 三层文件

原始文件和派生文件分开保存，不覆盖原始扫描本：

```text
data/raw/canon/                 # 原始 PDF、网页抓取结果
data/processed/canon/ocr/       # 每页 OCR 原始 JSONL、纯文本
data/processed/canon/verified/  # 人工核对后的转录文本
data/processed/canon/diff/      # OCR 与网页文本的差异报告
```

每次 OCR 至少记录：

| 字段 | 含义 |
|---|---|
| `source_id` | 版本唯一编号 |
| `input_sha256` | 输入 PDF 或页面图像哈希 |
| `page_pdf` | PDF 页码 |
| `page_printed` | 书内页码；看不清时为空 |
| `engine` | OCR 引擎，例如 PaddleOCR |
| `model` | 模型名称和版本 |
| `text_raw` | 未人工修改的 OCR 结果 |
| `blocks` | 文字框、顺序和识别分数 |
| `ocr_status` | `raw`、`reviewed`、`unreadable` |

建议同时保存页面 PNG。这样某个字出现争议时，可以直接定位到扫描页和文字框，而不是只看一份被清洗过的 TXT。

## 3. 推荐处理流程

### 3.1 先检查 PDF 是否已有文字层

```bash
pdftotext -layout input.pdf output.txt
```

如果能提取出文字，也只把它当作辅助录入本；仍需抽查扫描页。很多古籍 PDF 的文字层来自旧 OCR，可能存在错序、漏字和现代标点。

### 3.2 没有文字层时做页面 OCR

先把页面按约 300 DPI 转为图片，再使用支持中文和版面分析的 OCR。当前建议优先使用 PaddleOCR；需要生成“可搜索 PDF”时可另用 OCRmyPDF，但该 PDF 仍是派生物，不作为版本底本。PaddleOCR 官方文档提供中文 OCR 与版面分析流程，OCRmyPDF 官方文档说明其主要作用是给扫描 PDF 增加可搜索文字层。[PaddleOCR OCR 文档](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)、[OCRmyPDF 文档](https://ocrmypdf.readthedocs.io/en/latest/)

项目入口是 [ocr_canon.py](../ocr_canon.py)。例如先只处理《子平真诠》前 3 页：

```bash
python3 ocr_canon.py \
  --input data/raw/canon/wikimedia/ziping_zhenquan_scan.pdf \
  --source-id ziping_zhenquan_scan_edition_a \
  --pages 1-3
```

结果写入 `data/processed/canon/ocr/<source_id>/`：`pages/` 是页面图像，`ocr.jsonl` 是带文字框和分数的原始结果，`ocr.txt` 是便于搜索的文本，`manifest.json` 记录输入哈希和运行参数。首次使用前需按 PaddleOCR 官方文档安装依赖；脚本未安装时会明确报错，不会静默退化为不可靠的文本。

古籍常见竖排、双栏、眉批和版心，不能只依赖整页纯文本输出。应尽量保留：

1. 页面坐标；
2. 栏目或文字块顺序；
3. 识别置信度；
4. 页眉、正文、夹注、批语的区域类型。

对于竖排页，先确认文字方向和栏顺序；方向错了时，应对页面或文字块旋转后重新识别。不得为了让句子通顺而直接重排原始 OCR 文件。

### 3.3 OCR 后的标准化

标准化只生成新文件，不修改 `text_raw`：

- 允许：统一换行、去除重复页眉、记录人工补字；
- 必须保留：繁简原貌、异体字、缺字、无法确认的字；
- 不允许自动完成：根据现代语义猜字、自动补句、把注释并入正文、把白话改写当作原文。

无法确认的字使用占位符，例如 `□`，并增加 `uncertain_char` 标记。规则摘录中如果关键字含有 `□`，不能进入 `verified` 或 `approved`。

## 4. OCR 与网上内容冲突时怎么处理

冲突本身不等于新规则，也不允许让模型自动选择“更通顺”的版本。

| 情况 | 处理 | 是否直接新增 rules |
|---|---|---|
| OCR 与网页不同，扫描页支持 OCR 修订 | 修正 OCR，保留原 OCR 和网页文本 | 否，除非校订后形成完整规则 |
| OCR 与网页不同，扫描页字迹不清 | 保存两个异文，标记 `unresolved` | 否 |
| 扫描本与网页本属于不同版本 | 分别记录版本和出处 | 不合并；可建立两个版本规则 |
| 网页本有现代标点或白话解释 | 作为辅助材料单独存放 | 不能作为古籍原文规则 |
| 原文与后世评注冲突 | 拆成原文层和评注层 | 不混成一条规则 |
| 同一句在不同流派中结论不同 | 分配不同 `school` 和 `rule_id` | 可以分别加入候选规则 |

证据优先级建议为：

```text
扫描页可辨识原文 > 有明确版本的影印/点校本 > 可追溯网页录入本
> 现代评注 > 白话文章、论坛和模型总结
```

这里的“优先”只表示文字校勘证据，不表示某个流派在统计上更正确。不同版本都可信时，应保留异文，不能强行合并成一条所谓标准规则。

## 5. 规则状态与进入条件

每条规则建议使用以下状态：

| 状态 | 含义 | 能否用于统计执行 |
|---|---|---|
| `extracted` | 从 OCR、网页或书中发现的候选句 | 否 |
| `candidate` | 已写成“条件 → 判断”，但证据或操作化未完成 | 否 |
| `unresolved` | 版本、字词或断句仍有冲突 | 否 |
| `verified` | 已回看扫描页并确认文字，来源位置完整 | 仅可做开发测试 |
| `approved` | 人工审核、条件和 Y 域已冻结 | 可以进入预注册统计 |
| `rejected` | 无法确认、不可操作化或明确不是规则 | 否 |
| `edition_variant` | 另一版本的独立表达 | 作为独立规则集处理 |

只有 `approved` 规则才能进入正式统计；`verified` 规则可以用于测试规则解释器，但不能在看到结果后继续修改条件。

## 6. 规则记录格式

规则不是把 OCR 句子直接复制到 YAML，而是保留证据、解释和计算定义三层：

```yaml
rule_id: ziping_original_001
school: 子平真诠原著
source_id: ziping_zhenquan_scan_edition_a
book: 子平真诠
edition: 国家图书馆扫描本
volume: null
chapter: 论用神
page_pdf: 42
page_printed: 18
quote_original: "原文逐字转录"
quote_ocr: "OCR 未校订文本"
quote_variants:
  - source_id: ziping_zhenquan_web_edition_b
    text: "网页异文"
    status: unresolved
modern_interpretation: "现代语言的最小解释"
if:
  - feature: month_order
    operator: equals
    value: example
then:
  - feature: use_element
    value: example
candidate_y:
  - career
operationalization: "明确输入字段、阈值、时间窗和缺失处理"
rule_status: candidate
```

`quote_ocr` 只记录机器结果，`quote_original` 才是人工校订后的转录。两者不同不能覆盖；这样以后换 OCR 模型或发现误校时仍能重现过程。

## 7. 判断“冲突是否形成新规则”的标准

只有同时满足以下条件，才可新增一条规则：

1. 冲突对应不同的原文、版本、注家或流派，而不是单纯 OCR 错字；
2. 能给出独立的 `source_id`、章节和页码；
3. 条件、判断和例外可以转成明确字段；
4. 与原规则的差异预先写明，不能只因某一版本在样本上效果更好才保留；
5. 分配独立的 `rule_id`，统计时作为不同规则或不同规则集比较。

例如：扫描页确认是“通关”，OCR 误识为“通关之神”，这不是新规则，只是 OCR 修订；若原著和后世评注分别给出不同的取用神体系，则是两个来源、两个流派规则，不能合并。

## 8. 本项目当前执行口径

现有 [古籍逻辑提取初稿](古籍逻辑提取初稿.md) 中的内容全部继续保持 `candidate`。下一步应先从每本书选少量高价值章节，完成“扫描页—OCR—网页文本—人工校订—Y 域—操作化”的闭环，再批量提取。模型可以帮助定位、比对和生成现代短释，但不能单独决定原文、版本归属或规则是否成立。
