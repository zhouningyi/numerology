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


def load_ocr() -> Any:
    """延迟导入 PaddleOCR，避免未安装时影响项目其他命令。"""
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise SystemExit(
            "未安装 PaddleOCR。请按官方文档安装后重试："
            " https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html"
        ) from exc
    return PaddleOCR(
        lang="ch",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
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
        return "\n".join(str(text) for text in texts), blocks, raw

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
) -> Path:
    """完成一批页面 OCR，并返回 manifest 路径。"""
    input_sha256 = sha256_file(pdf)
    first, last = parse_pages(pages)
    source_root = output_root / source_id
    page_root = source_root / "pages"
    result_path = source_root / "ocr.jsonl"
    text_path = source_root / "ocr.txt"
    page_images = render_pages(pdf, first, last, dpi, page_root)
    ocr = load_ocr()
    fetched_at = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []

    with result_path.open("w", encoding="utf-8") as jsonl, text_path.open(
        "w", encoding="utf-8"
    ) as text_file:
        for image_path in page_images:
            page_number = int(image_path.stem.rsplit("-", 1)[1])
            result = ocr.predict(str(image_path))
            text, blocks, raw = extract_text(result)
            record = {
                "source_id": source_id,
                "input_pdf": str(pdf),
                "input_sha256": input_sha256,
                "page_pdf": page_number,
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

    manifest = {
        "source_id": source_id,
        "input_pdf": str(pdf),
        "input_sha256": input_sha256,
        "pages": [record["page_pdf"] for record in records],
        "dpi": dpi,
        "engine": "PaddleOCR",
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
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/canon/ocr"))
    args = parser.parse_args()
    manifest = run_ocr(
        args.input.resolve(),
        args.source_id,
        args.pages,
        args.dpi,
        args.output_root,
    )
    print(f"OCR 完成：{manifest}")


if __name__ == "__main__":
    main()
