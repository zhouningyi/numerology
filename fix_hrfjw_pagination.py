#!/usr/bin/env python3
"""补爬 hrfjw.com 洪启嵩译华严经的分页内容。

原爬取只拿了每卷第 1 页，偈颂译文多在第 2 页起——这是偈颂章对齐
补译率高的根因。本脚本对 modern_layers 中每个 source_url：
1. 抓第 1 页找分页链接（180764_2.html 式），抓所有后续页；
2. 提取正文（GBK 解码，去站点导航，截到"上一页/下一页"）；
3. 把缺失部分追加进对应 modern_layers 行（备份原文件）。
"""

from __future__ import annotations

import json
import re
import time
from html import unescape
from pathlib import Path

import requests

MODERN = Path("data/processed/canon/layers/huayan_t0279_modern_layers.jsonl")
UA = {"User-Agent": "Mozilla/5.0 (research; local snapshot)"}
PAGE_RE_TMPL = r'href="([^"]*{stem}_\d+\.html)"'


def page_text(raw_bytes: bytes) -> str:
    raw = raw_bytes.decode("gb18030", errors="replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # 正文起点：跳过站点导航（到含"当前位置"或标题重复行之后），终点：分页条
    joined = "\n".join(lines)
    for cut_marker in ("上一页", "下一页", "保存在桌面"):
        pos = joined.find(cut_marker)
        if pos > 0:
            joined = joined[:pos]
    return joined


def fetch(session: requests.Session, url: str) -> bytes:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def main() -> None:
    rows = [json.loads(l) for l in MODERN.open(encoding="utf-8")]
    backup = MODERN.with_suffix(".jsonl.pre-pagination.bak")
    backup.write_text(MODERN.read_text(encoding="utf-8"), encoding="utf-8")
    session = requests.Session()
    session.headers.update(UA)

    patched = 0
    for row in rows:
        url = row.get("source_url") or ""
        match = re.search(r"/(\d+)\.html?$", url)
        if not match:
            continue
        stem = match.group(1)
        try:
            first = fetch(session, url)
        except requests.RequestException as exc:
            print(f"跳过 {url}: {exc}")
            continue
        raw = first.decode("gb18030", errors="replace")
        extra_pages = sorted(set(re.findall(PAGE_RE_TMPL.format(stem=stem), raw)))
        if not extra_pages:
            time.sleep(0.4)
            continue
        appended = []
        for page_url in extra_pages:
            try:
                content = page_text(fetch(session, page_url))
            except requests.RequestException as exc:
                print(f"分页失败 {page_url}: {exc}")
                continue
            # 只保留存量块之后的新内容：粗暴起见整页正文追加，
            # 后续 split_sentences 去重风险低（对齐按窗口选句）
            stored_tail = re.sub(r"\s+", "", row["text"])[-24:]
            normalized = re.sub(r"\s+", "", content)
            if stored_tail and stored_tail in normalized:
                cut = normalized.find(stored_tail) + len(stored_tail)
                # 映射回原文本近似位置：直接用未归一化文本按比例截
                ratio = cut / max(1, len(normalized))
                content = content[int(len(content) * ratio):]
            appended.append(content)
            time.sleep(0.4)
        if appended:
            row["text"] = row["text"] + "\n" + "\n".join(appended)
            row["pagination_fixed"] = True
            patched += 1
            print(f"{url}: 补 {len(extra_pages)} 页，新增 {sum(len(a) for a in appended)} 字")
        time.sleep(0.4)

    with MODERN.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"完成：{patched}/{len(rows)} 块补页 -> {MODERN}（备份 {backup.name}）")


if __name__ == "__main__":
    main()
