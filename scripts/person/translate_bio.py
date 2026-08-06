#!/usr/bin/env python3
"""批量翻译传记：English → 中文，使用 GPT-4o-mini。

用法:
    export OPENAI_API_KEY=sk-...
    python translate_bio.py                # 翻译全部未翻译的
    python translate_bio.py --limit 100    # 只翻译 100 条
    python translate_bio.py --batch-size 5 # 每批 5 条（默认 10）
    python translate_bio.py --dry-run      # 只统计，不调用 API
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "numerology.db"

SYSTEM_PROMPT = """你是一个专业翻译。将以下英文传记翻译为中文。
要求：
- 保持人名、地名的英文原文，必要时在括号内给出中文（如 "Albert Einstein（阿尔伯特·爱因斯坦）" 首次出现时标注，之后直接用原名）
- 保持年份、日期等数字不变
- 翻译要自然流畅，不要逐字翻译
- 只输出翻译结果，不要加任何解释"""


def get_untranslated(conn: sqlite3.Connection, limit: int | None = None):
    """获取尚未翻译的传记记录。"""
    sql = """
        SELECT id, name, biography FROM persons
        WHERE biography IS NOT NULL AND biography != ''
          AND (biography_zh IS NULL OR biography_zh = '')
        ORDER BY id
    """
    if limit:
        sql += f" LIMIT {limit}"
    return conn.execute(sql).fetchall()


def translate_batch(
    client: OpenAI, rows: list, model: str = "gpt-4o-mini"
) -> list[tuple[int, str]]:
    """翻译一批记录，返回 [(person_id, translation), ...]。

    每条传记独立翻译（不合并），避免混淆。
    """
    results = []
    for row in rows:
        person_id, name, bio = row["id"], row["name"], row["biography"]
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": bio},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            translation = resp.choices[0].message.content.strip()
            results.append((person_id, translation))
        except Exception as e:
            logger.error(f"翻译失败 [{person_id}] {name}: {e}")
    return results


def main():
    parser = argparse.ArgumentParser(description="批量翻译传记为中文")
    parser.add_argument("--db", default=str(DB_PATH), help="数据库路径")
    parser.add_argument("--limit", type=int, default=None, help="最多翻译条数")
    parser.add_argument("--batch-size", type=int, default=10, help="每批条数（默认10）")
    parser.add_argument(
        "--model", default="gpt-4o-mini", help="模型名称（默认 gpt-4o-mini）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不翻译")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and not args.dry_run:
        logger.error("请设置 OPENAI_API_KEY 环境变量")
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = get_untranslated(conn, args.limit)
    total = len(rows)

    if args.dry_run:
        total_chars = sum(len(r["biography"]) for r in rows)
        est_tokens = total_chars // 4
        est_cost = est_tokens / 1_000_000 * 0.15 + (est_tokens * 2) / 1_000_000 * 0.60
        logger.info(f"待翻译: {total} 条, {total_chars:,} 字符, ~{est_tokens:,} tokens")
        logger.info(f"预估费用 ({args.model}): ${est_cost:.2f}")
        conn.close()
        return

    client = OpenAI(api_key=api_key)
    translated = 0
    failed = 0

    logger.info(
        f"开始翻译 {total} 条传记 (model={args.model}, batch={args.batch_size})"
    )

    for i in range(0, total, args.batch_size):
        batch = rows[i : i + args.batch_size]
        results = translate_batch(client, batch, model=args.model)

        for person_id, translation in results:
            conn.execute(
                "UPDATE persons SET biography_zh = ? WHERE id = ?",
                (translation, person_id),
            )
            translated += 1

        failed += len(batch) - len(results)
        conn.commit()

        logger.info(f"进度: {translated}/{total} 翻译完成, {failed} 失败")

    conn.close()
    logger.info(f"完成. 翻译: {translated}, 失败: {failed}")


if __name__ == "__main__":
    main()
