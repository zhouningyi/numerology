#!/usr/bin/env python3
"""自动挖掘濒死「可抽象现象」候选。

两路并行（分层，不混为一谈）：

1. **问卷未映射题**  
   扫 experiences.jsonl 的 qa，找出高频、尚未被 phenomena.yaml 覆盖的问题，
   估算阳性率，输出可粘贴进 phenomena 的候选规则。

2. **叙述正文母题**  
   用 motifs.yaml 现有规则统计覆盖；并用简单 n-gram / 种子扩展
   提案新的正文短语母题（candidate only）。

输出：
  data/audits/nde_phenomena_mine_<ts>.json
  data/audits/nde_phenomena_mine_latest.json

示例：
  python3 -m scripts.nde.mine_nde_phenomena
  python3 -m scripts.nde.mine_nde_phenomena --min-q-freq 400 --top 40
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from numerology.nde.parser import load_motifs, load_phenomena, tag_motifs

EXPERIENCES = Path("data/processed/nderf/experiences.jsonl")
AUDITS = Path("data/audits")
PHENOMENA_PATH = Path("numerology/nde/phenomena.yaml")
MOTIFS_PATH = Path("numerology/nde/motifs.yaml")

# 问卷题中明显的元数据/非现象题，挖掘时降权
_META_Q = (
    "background information",
    "gender",
    "date nde",
    "religion prior",
    "religion now",
    "nde elements",
    "after the nde",
    "god, spiritual",
    "the experience included",
    "how accurately do you remember",
    "questions asked and information",
    "anything else that you would like",
    "other questions that we could ask",
    "associated life-threatening event",
    "shared this experience",
    "knowledge of near death",
    "age at the time",
)


def _stem_q(q: str) -> str:
    q = re.sub(r"\s+", " ", (q or "").strip().lower())
    return re.sub(r"[^a-z0-9 ?/'%-]+", "", q)[:140]


def _is_meta(q: str) -> bool:
    return any(m in q for m in _META_Q)


def covered_needles() -> set[str]:
    needles = set()
    for spec in load_phenomena().values():
        for rule in spec.get("match") or []:
            needles.add(str(rule.get("question_contains") or "").lower())
    return needles


def mine_questionnaire(rows: list[dict], *, min_freq: int, top: int) -> list[dict]:
    needles = covered_needles()
    freq = Counter()
    pos = defaultdict(lambda: [0, 0])  # yesish, total
    samples = defaultdict(list)

    for row in rows:
        for pair in row.get("qa") or []:
            q = _stem_q(pair.get("q") or "")
            if len(q) < 20 or _is_meta(q):
                continue
            # 已被 phenomena 覆盖？
            if any(n and n in q for n in needles):
                continue
            freq[q] += 1
            a = (pair.get("a") or "").strip()
            pos[q][1] += 1
            al = a.lower()
            if al and not al.startswith(("no", "uncertain", "none", "n/a", "unknown")):
                pos[q][0] += 1
            if len(samples[q]) < 3 and a:
                samples[q].append(a[:120].replace("\n", " "))

    out = []
    for q, c in freq.most_common(top * 3):
        if c < min_freq:
            break
        y, t = pos[q]
        rate = y / max(1, t)
        # 建议 key：取问题中有信息量的词
        key_hint = re.sub(r"[^a-z]+", "_", q)[:40].strip("_")
        out.append({
            "question_stem": q,
            "freq": c,
            "positive_rate_est": round(rate, 3),
            "suggested_key": key_hint,
            "suggested_rule": {
                "match": [{"question_contains": q[:60]}],
            },
            "answer_samples": samples[q],
            "status": "candidate",
            "layer": "survey_phenomenon",
        })
        if len(out) >= top:
            break
    return out


def mine_motif_coverage(rows: list[dict]) -> dict:
    motifs = load_motifs()
    counts = Counter()
    for row in rows:
        tagged = tag_motifs(row.get("description") or "", motifs)
        for key in tagged:
            counts[key] += 1
    catalog = []
    for key, spec in motifs.items():
        catalog.append({
            "key": key,
            "name": spec.get("name"),
            "group": spec.get("group"),
            "hits": counts.get(key, 0),
            "rate": round(counts.get(key, 0) / max(1, len(rows)), 4),
        })
    catalog.sort(key=lambda x: -x["hits"])
    return {"total_cases": len(rows), "motifs": catalog}


def mine_new_phrases(
    rows: list[dict],
    *,
    min_df: int = 80,
    top: int = 40,
) -> list[dict]:
    """极简短语挖掘：从叙述中抽 2–4 词英文短语，过滤停用与已有 motif。"""
    existing = set()
    for spec in load_motifs().values():
        for p in spec.get("patterns") or []:
            existing.add(p.lower())

    stop = {
        "the", "and", "that", "was", "were", "with", "from", "this", "have",
        "had", "for", "not", "but", "are", "his", "her", "they", "them",
        "you", "your", "into", "then", "when", "what", "there", "their",
        "about", "would", "could", "been", "being", "very", "just", "like",
        "all", "out", "can", "said", "felt", "feel", "know", "knew", "see",
        "saw", "experience", "life", "time", "back", "body", "light",
        "next", "thing", "found", "myself", "years", "old", "even", "though",
        "more", "than", "didn't", "want", "looking", "down", "emergency",
        "room", "after", "before", "around", "through", "over", "under",
        "still", "also", "only", "some", "many", "much", "most", "other",
        "came", "went", "told", "went", "come", "going", "something",
    }
    # 已有现象大词
    stop |= {"tunnel", "beings", "deceased", "god", "heaven"}

    phrase_df = Counter()
    phrase_examples = defaultdict(list)
    token_re = re.compile(r"[a-z][a-z'-]{2,}")

    for row in rows:
        text = (row.get("description") or "").lower()
        if len(text) < 80:
            continue
        tokens = token_re.findall(text)
        seen_here = set()
        for n in (2, 3):
            for i in range(len(tokens) - n + 1):
                grams = tokens[i : i + n]
                if any(t in stop for t in grams):
                    continue
                if grams[0] in stop or grams[-1] in stop:
                    continue
                phrase = " ".join(grams)
                if phrase in existing:
                    continue
                if phrase in seen_here:
                    continue
                seen_here.add(phrase)
                phrase_df[phrase] += 1
                if len(phrase_examples[phrase]) < 2:
                    # 证据上下文
                    idx = text.find(phrase)
                    if idx >= 0:
                        snip = text[max(0, idx - 30) : idx + len(phrase) + 30]
                        phrase_examples[phrase].append(snip.replace("\n", " ")[:140])

    candidates = []
    for phrase, df in phrase_df.most_common(top * 5):
        if df < min_df:
            break
        # 过滤过泛 / 叙事粘合词
        if phrase in {
            "i was", "i could", "i felt", "it was", "i had", "i saw",
            "next thing", "found myself", "years old", "even though",
            "more than", "looking down", "emergency room", "didn't want",
        }:
            continue
        if phrase.startswith(("i ", "it ", "my ", "me ", "we ", "he ", "she ")):
            continue
        candidates.append({
            "phrase": phrase,
            "doc_freq": df,
            "rate": round(df / max(1, len(rows)), 4),
            "examples": phrase_examples[phrase],
            "suggested_motif": {
                "name": phrase,
                "patterns": [phrase],
                "group": "mined",
                "status": "candidate",
            },
            "layer": "text_motif",
            "status": "candidate",
        })
        if len(candidates) >= top:
            break
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-q-freq", type=int, default=400)
    parser.add_argument("--min-phrase-df", type=int, default=100)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--limit-docs", type=int, default=None)
    args = parser.parse_args()

    if not EXPERIENCES.exists():
        raise SystemExit(f"缺少 {EXPERIENCES}")
    rows = [
        json.loads(line)
        for line in EXPERIENCES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit_docs:
        rows = rows[: args.limit_docs]

    report = {
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "n_experiences": len(rows),
        "existing_survey_phenomena": len(load_phenomena()),
        "existing_motifs": len(load_motifs()),
        "unmapped_questionnaire_candidates": mine_questionnaire(
            rows, min_freq=args.min_q_freq, top=args.top,
        ),
        "motif_coverage": mine_motif_coverage(rows),
        "new_phrase_candidates": mine_new_phrases(
            rows, min_df=args.min_phrase_df, top=args.top,
        ),
        "how_to_promote": {
            "survey": "把 unmapped_questionnaire_candidates 里确认的规则写入 numerology/nde/phenomena.yaml，再跑 reclassify_nde.py",
            "motif": "把 new_phrase_candidates 确认后写入 numerology/nde/motifs.yaml，再跑 reclassify_nde.py --motifs",
            "note": "问卷现象可进 /nde 大类统计；正文母题默认 candidate，适合发现「白光/音乐/光之城」等细粒度母题",
        },
    }

    AUDITS.mkdir(parents=True, exist_ok=True)
    stamp = report["checked_at"].replace(":", "").replace("+00:00", "Z")
    path = AUDITS / f"nde_phenomena_mine_{stamp}.json"
    latest = AUDITS / "nde_phenomena_mine_latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")

    summary = {
        "path": str(path),
        "latest": str(latest),
        "survey_candidates": len(report["unmapped_questionnaire_candidates"]),
        "phrase_candidates": len(report["new_phrase_candidates"]),
        "top_motifs": report["motif_coverage"]["motifs"][:8],
        "top_survey": [
            {
                "q": c["question_stem"][:70],
                "freq": c["freq"],
                "pos": c["positive_rate_est"],
            }
            for c in report["unmapped_questionnaire_candidates"][:8]
        ],
        "top_phrases": [
            {"phrase": c["phrase"], "df": c["doc_freq"]}
            for c in report["new_phrase_candidates"][:8]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
