#!/usr/bin/env python3
"""对古籍扫描 PDF 做逐页 OCR，并保存可追溯的原始结果。

本脚本只生成 OCR 派生文件，不修改扫描 PDF，也不写入命理规则表。
需要先安装 PaddleOCR；未安装时脚本会给出安装提示。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    """计算输入文件哈希，作为 OCR 快照的内容身份。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_pages(value: str, total_pages: int | None = None) -> tuple[int, int]:
    """解析单页或页码范围，页码按 PDF 的 1-based 页码处理。"""
    if "-" in value:
        first_text, last_text = value.split("-", 1)
        first, last = int(first_text), int(last_text)
    else:
        first = last = int(value)
    if first < 1 or last < first:
        raise ValueError("页码必须是正整数范围，例如 1 或 10-12")
    if total_pages is not None and last > total_pages:
        raise ValueError(f"页码 {last} 超过 PDF 总页数 {total_pages}")
    return first, last


def render_pages(pdf: Path, first: int, last: int, dpi: int, output: Path) -> list[Path]:
    """使用 Poppler 将指定 PDF 页转为 PNG。"""
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="numerology-ocr-") as temporary:
        prefix = Path(temporary) / "page"
        command = [
            "pdftoppm",
            "-f",
            str(first),
            "-l",
            str(last),
            "-r",
            str(dpi),
            "-png",
            str(pdf),
            str(prefix),
        ]
        subprocess.run(command, check=True)
        rendered = sorted(Path(temporary).glob("page-*.png"))
        if not rendered:
            raise RuntimeError("pdftoppm 没有生成页面图片")
        pages: list[Path] = []
        for index, page in enumerate(rendered, start=first):
            destination = output / f"page-{index:04d}.png"
            shutil.copy2(page, destination)
            pages.append(destination)
        return pages


def jsonable(value: Any) -> Any:
    """尽量把不同 PaddleOCR 版本的结果对象转换为普通 JSON。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    to_list = getattr(value, "tolist", None)
    if callable(to_list):
        return jsonable(to_list())
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    for method in ("json", "to_json"):
        candidate = getattr(value, method, None)
        if callable(candidate):
            result = candidate()
            if isinstance(result, str):
                return json.loads(result)
            return jsonable(result)
    return str(value)


def compact_raw(value: Any) -> Any:
    """去掉 Paddle 返回的整张中间图片，只保留可追溯的识别元数据。"""
    if isinstance(value, dict):
        compacted = {}
        for key, item in value.items():
            if key in {"output_img", "input_img", "preprocessed_img"}:
                continue
            compacted[key] = compact_raw(item)
        return compacted
    if isinstance(value, list):
        return [compact_raw(item) for item in value]
    return value


def load_ocr(textline_orientation: bool = True, mobile: bool = False, rec_batch_size: int = 1) -> Any:
    """延迟导入 PaddleOCR，避免未安装时影响项目其他命令。"""
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise SystemExit(
            "未安装 PaddleOCR。请按官方文档安装后重试："
            " https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html"
        ) from exc
    options = {
        "lang": "ch",
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": textline_orientation,
    }
    if mobile:
        options.update(
            {
                "text_detection_model_name": "PP-OCRv5_mobile_det",
                "text_recognition_model_name": "PP-OCRv5_mobile_rec",
                "text_recognition_batch_size": rec_batch_size,
            }
        )
    return PaddleOCR(
        **options,
    )


def extract_text(result: Any) -> tuple[str, list[dict[str, Any]], Any]:
    """从 PaddleOCR 新旧结果中提取文本、文字框和原始可序列化结果。"""
    raw = jsonable(result)
    payload = raw
    if isinstance(raw, list) and len(raw) == 1:
        payload = raw[0]
    if isinstance(payload, dict) and isinstance(payload.get("res"), dict):
        payload = payload["res"]

    if isinstance(payload, dict) and "rec_texts" in payload:
        texts = payload.get("rec_texts", [])
        scores = payload.get("rec_scores", [])
        boxes = payload.get("rec_boxes", payload.get("dt_polys", []))
        blocks = []
        for index, text in enumerate(texts):
            blocks.append(
                {
                    "text": str(text),
                    "score": scores[index] if index < len(scores) else None,
                    "box": boxes[index] if index < len(boxes) else None,
                }
            )
        # 古籍常为竖排版：文字框高度大于宽度时，按右列到左列排序；
        # 横排页面则按上到下、左到右排序。原始结果仍完整保留。
        if blocks:
            def box_metrics(box: Any) -> tuple[float, float, float]:
                if not isinstance(box, list) or not box:
                    return (0.0, 0.0, 0.0)
                points = box if isinstance(box[0], list) else [box]
                xs = [float(point[0]) for point in points if len(point) >= 2]
                ys = [float(point[1]) for point in points if len(point) >= 2]
                if not xs or not ys:
                    return (0.0, 0.0, 0.0)
                return (sum(xs) / len(xs), sum(ys) / len(ys), max(ys) - min(ys))

            metrics = [box_metrics(block["box"]) for block in blocks]
            vertical = sum(height > 1 for _, _, height in metrics) >= len(blocks) / 2
            if vertical:
                blocks.sort(key=lambda block: (-box_metrics(block["box"])[0], box_metrics(block["box"])[1]))
            else:
                blocks.sort(key=lambda block: (box_metrics(block["box"])[1], box_metrics(block["box"])[0]))
        return "\n".join(block["text"] for block in blocks), blocks, raw

    # 兼容旧版返回：[[[box], (text, score)], ...]。
    if isinstance(payload, list):
        blocks = []
        for item in payload:
            if not isinstance(item, list) or len(item) < 2:
                continue
            box, recognition = item[0], item[1]
            if isinstance(recognition, (list, tuple)) and recognition:
                blocks.append(
                    {
                        "text": str(recognition[0]),
                        "score": recognition[1] if len(recognition) > 1 else None,
                        "box": box,
                    }
                )
        return "\n".join(item["text"] for item in blocks), blocks, raw

    return "", [], raw


def run_ocr(
    pdf: Path,
    source_id: str,
    pages: str,
    dpi: int,
    output_root: Path,
    chapter: int | None = None,
    append: bool = False,
    textline_orientation: bool = True,
    mobile: bool = False,
    skip_render: bool = False,
    image_root: Path | None = None,
    rec_batch_size: int = 1,
) -> Path:
    """完成一批页面 OCR，并返回 manifest 路径。"""
    input_sha256 = sha256_file(pdf)
    first, last = parse_pages(pages)
    source_root = output_root / source_id
    source_root.mkdir(parents=True, exist_ok=True)
    page_root = image_root or (source_root / "pages")
    result_path = source_root / "ocr.jsonl"
    text_path = source_root / "ocr.txt"
    existing_pages: set[int] = set()
    if append and result_path.exists():
        with result_path.open(encoding="utf-8") as existing_file:
            for line in existing_file:
                if not line.strip():
                    continue
                try:
                    page_number = json.loads(line).get("page_pdf")
                    if page_number is not None:
                        existing_pages.add(int(page_number))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
    if skip_render:
        page_images = [page_root / f"page-{page:04d}.png" for page in range(first, last + 1)]
        missing = [path for path in page_images if not path.exists()]
        if missing:
            raise FileNotFoundError(f"--skip-render 缺少页面图片，例如：{missing[0]}")
    else:
        page_images = render_pages(pdf, first, last, dpi, page_root)
    ocr = load_ocr(
        textline_orientation=textline_orientation,
        mobile=mobile,
        rec_batch_size=rec_batch_size,
    )
    fetched_at = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []

    jsonl_mode = "a" if append else "w"
    text_mode = "a" if append else "w"
    with result_path.open(jsonl_mode, encoding="utf-8") as jsonl, text_path.open(
        text_mode, encoding="utf-8"
    ) as text_file:
        for image_path in page_images:
            page_number = int(image_path.stem.rsplit("-", 1)[1])
            if page_number in existing_pages:
                continue
            result = ocr.predict(str(image_path))
            text, blocks, raw = extract_text(result)
            raw = compact_raw(raw)
            record = {
                "source_id": source_id,
                "input_pdf": str(pdf),
                "input_sha256": input_sha256,
                "page_pdf": page_number,
                "chapter": chapter,
                "image": str(image_path),
                "engine": "PaddleOCR",
                "model": "runtime-default",
                "fetched_at": fetched_at,
                "ocr_status": "raw",
                "text_raw": text,
                "blocks": blocks,
                "raw_result": raw,
            }
            jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")
            text_file.write(f"\n\n===== PDF 第 {page_number} 页 =====\n{text}\n")
            records.append(record)

    manifest_pages = sorted(existing_pages | {record["page_pdf"] for record in records})
    manifest = {
        "source_id": source_id,
        "input_pdf": str(pdf),
        "input_sha256": input_sha256,
        "pages": manifest_pages,
        "chapter": chapter,
        "dpi": dpi,
        "engine": "PaddleOCR",
        "textline_orientation": textline_orientation,
        "mobile": mobile,
        "text_recognition_batch_size": rec_batch_size,
        "created_at": fetched_at,
        "ocr_jsonl": str(result_path),
        "ocr_text": str(text_path),
    }
    manifest_path = source_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="扫描 PDF")
    parser.add_argument("--source-id", required=True, help="版本唯一编号")
    parser.add_argument("--pages", default="1", help="PDF 页码，例如 1 或 10-12")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--chapter", type=int, help="本批页面对应的互联网版本章节")
    parser.add_argument("--append", action="store_true", help="追加到已有 OCR；已存在的 PDF 页会跳过")
    parser.add_argument(
        "--no-textline-orientation",
        action="store_true",
        help="批量 OCR 加速：关闭文字行方向分类；扫描页方向已知时使用",
    )
    parser.add_argument(
        "--mobile",
        action="store_true",
        help="使用 PP-OCRv5 mobile 检测/识别模型，适合大批量初筛",
    )
    parser.add_argument(
        "--skip-render",
        action="store_true",
        help="直接使用 output-root/source-id/pages 中已有的页面图片",
    )
    parser.add_argument(
        "--image-root",
        type=Path,
        help="指定已有页图目录；仅与 --skip-render 配合使用",
    )
    parser.add_argument("--rec-batch-size", type=int, default=1, help="轻量模型文字识别批大小")
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/canon/ocr"))
    args = parser.parse_args()
    manifest = run_ocr(
        args.input.resolve(),
        args.source_id,
        args.pages,
        args.dpi,
        args.output_root,
        args.chapter,
        args.append,
        not args.no_textline_orientation,
        args.mobile,
        args.skip_render,
        args.image_root,
        args.rec_batch_size,
    )
    print(f"OCR 完成：{manifest}")


if __name__ == "__main__":
    main()
