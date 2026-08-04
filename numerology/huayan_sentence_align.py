"""华严经句句对应：单调强制对齐（主）+ 本地模型裁决（辅）。

不做「整章让模型自由编号」——那会漂移、吞导航、变繁简转换。
正确路径：

1. 原文 / 参考白话各自切句；
2. 章内单调 DP：每句原文对应 1..K 句连续白话（允许白话更碎）；
3. 低分句再交给本地 Ollama，只在候选窗口内选号；
4. 输出 pairs=[[原句, 译句], ...]，由构造保证一一挂接。

「训练本地模型」：对本任务性价比低。若以后要训，应在本模块产出的
verified pairs 上做 LoRA，而不是先盲训再对齐。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from zhconv import convert as zh_convert
except ImportError:
    def zh_convert(text: str, _target: str) -> str:  # type: ignore[misc]
        return text

from numerology.translation_display import contains_web_junk, is_simplified_only


_SENT_RE = re.compile(r"[^。！？；\n]*[。！？；]」?|[^。！？；\n]+")
_JUNK_LINE = re.compile(
    r"(\[详情\]|放大字体|缩小|关闭|【原典】|作者：|\[投稿\]|白话华严|"
    r"华严经是大乘|首页|目录|上一|下一|返回)"
)


def split_classic_sentences(text: str, max_len: int = 120) -> list[str]:
    """古文切句：句号/叹问/分号/换行；超长再按逗号切。"""
    text = (text or "").strip()
    if not text:
        return []
    raw = [s.strip() for s in _SENT_RE.findall(text) if s and s.strip()]
    out: list[str] = []
    for sentence in raw:
        while len(sentence) > max_len:
            cut = max(
                sentence.rfind("，", 0, max_len),
                sentence.rfind("、", 0, max_len),
                sentence.rfind("：", 0, max_len),
            )
            if cut < max_len // 3:
                cut = max_len
            out.append(sentence[: cut + 1].strip())
            sentence = sentence[cut + 1 :].strip()
        if sentence:
            out.append(sentence)
    return out


def clean_reference_text(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if _JUNK_LINE.search(s) or contains_web_junk(s):
            continue
        if s in {"华严经", "原文", "译文", "入门", "讲解", "问答", "文章", "正常"}:
            continue
        lines.append(s)
    return "\n".join(lines)


def _han(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fff]", "", zh_convert(text or "", "zh-cn"))


def _bigrams(text: str) -> set[str]:
    h = _han(text)
    if len(h) < 2:
        return {h} if h else set()
    return {h[i : i + 2] for i in range(len(h) - 1)}


def lexical_score(original: str, modern: str) -> float:
    """原文↔白话相似度：二元组重叠为主，长度比为辅。越高越好。"""
    if not original or not modern:
        return -5.0
    if contains_web_junk(modern) or _JUNK_LINE.search(modern):
        return -8.0
    if is_simplified_only(original, modern):
        # 伪繁简：仍给一点分，但远低于真白话
        return -1.5
    o_han, m_han = _han(original), _han(modern)
    # 开经定式：词汇重叠低，但语义固定
    if "如是我闻" in o_han and any(k in modern for k in ("听闻", "这部经典", "我阿难", "如是我闻")):
        return 2.4
    if o_han.startswith("一时") and any(k in modern for k in ("当时", "那时", "佛陀", "摩竭")):
        return max(1.6, 0.0)  # 下限，后面仍可与 jacc 取高
    ob, mb = _bigrams(original), _bigrams(modern)
    if not ob or not mb:
        return -3.0
    inter = len(ob & mb)
    union = len(ob | mb)
    jacc = inter / max(1, union)
    # 白话通常更长：理想 modern_han / orig_han ≈ 1.2–3.5
    oh, mh = len(o_han), len(m_han)
    ratio = mh / max(1, oh)
    if ratio < 0.4:
        len_term = -1.2
    elif ratio > 8:
        len_term = -0.8
    elif 0.9 <= ratio <= 4.0:
        len_term = 0.35
    else:
        len_term = 0.0
    # 专名命中加成（>=2 字连续保留）
    bonus = 0.0
    if inter >= 3:
        bonus += 0.15
    if inter >= 6:
        bonus += 0.15
    score = 3.0 * jacc + len_term + bonus
    if o_han.startswith("一时") and any(k in modern for k in ("当时", "那时", "佛陀", "摩竭")):
        score = max(score, 1.8)
    return score


@dataclass
class AlignResult:
    pairs: list[list[str]]          # [[orig, modern], ...]
    scores: list[float]
    method: str                     # force_dp | ollama | mixed
    low_confidence: list[int]       # pair indices needing review


def force_align_sentences(
    orig_sents: list[str],
    ref_sents: list[str],
    *,
    max_ref_span: int = 5,
    skip_ref_cost: float = 0.08,
    empty_align_penalty: float = 1.8,
) -> AlignResult:
    """章/段内单调 DP：第 i 句原文 → 连续若干句白话。"""
    n, m = len(orig_sents), len(ref_sents)
    if n == 0:
        return AlignResult([], [], "force_dp", [])
    if m == 0:
        pairs = [[o, ""] for o in orig_sents]
        return AlignResult(pairs, [-empty_align_penalty] * n, "force_dp", list(range(n)))

    # 预计算简体汉字与二元组，避免 DP 内反复 zhconv
    orig_han = [_han(s) for s in orig_sents]
    ref_han = [_han(s) for s in ref_sents]
    orig_bi = [_bigrams(s) for s in orig_sents]
    ref_bi = [_bigrams(s) for s in ref_sents]
    # 前缀拼接的 bigram 并集：span 用 union
    ref_prefix: list[str] = [""]
    for h in ref_han:
        ref_prefix.append(ref_prefix[-1] + h)

    def score_span_fast(i: int, j0: int, j1: int) -> float:
        chunk = "".join(ref_sents[j0:j1])
        if contains_web_junk(chunk) or _JUNK_LINE.search(chunk):
            return -8.0
        o, oh = orig_sents[i], orig_han[i]
        # ref_prefix[j] = concat(ref_han[0:j])
        mh = ref_prefix[j1][len(ref_prefix[j0]):]
        if is_simplified_only(o, chunk):
            return -1.5
        if "如是我闻" in oh and any(k in chunk for k in ("听闻", "这部经典", "我阿难", "如是我闻")):
            return 2.4
        ob = orig_bi[i]
        mb: set[str] = set()
        for t in range(j0, j1):
            mb |= ref_bi[t]
        if not ob or not mb:
            return -3.0
        inter = len(ob & mb)
        jacc = inter / max(1, len(ob | mb))
        oh_n, mh_n = len(oh), len(mh)
        ratio = mh_n / max(1, oh_n)
        if ratio < 0.4:
            len_term = -1.2
        elif ratio > 8:
            len_term = -0.8
        elif 0.9 <= ratio <= 4.0:
            len_term = 0.35
        else:
            len_term = 0.0
        bonus = 0.15 if inter >= 3 else 0.0
        if inter >= 6:
            bonus += 0.15
        score = 3.0 * jacc + len_term + bonus
        if oh.startswith("一时") and any(k in chunk for k in ("当时", "那时", "佛陀", "摩竭")):
            score = max(score, 1.8)
        return score

    neg = -1e12
    dp = [[neg] * (m + 1) for _ in range(n + 1)]
    bt: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for j in range(1, m + 1):
        # 允许开头丢掉参考里的导语
        dp[0][j] = dp[0][j - 1] - skip_ref_cost
        bt[0][j] = ("skip_ref", j - 1)

    for i in range(1, n + 1):
        for j in range(0, m + 1):
            # 原文无对应白话（硬缺口）
            if dp[i - 1][j] + (-empty_align_penalty) > dp[i][j]:
                dp[i][j] = dp[i - 1][j] - empty_align_penalty
                bt[i][j] = ("empty",)
            if j == 0:
                continue
            max_k = min(max_ref_span, j)
            for k in range(1, max_k + 1):
                sc = score_span_fast(i - 1, j - k, j)
                # 多句白话略惩罚，避免一原文吞掉整段
                sc -= 0.04 * (k - 1)
                cand = dp[i - 1][j - k] + sc
                if cand > dp[i][j]:
                    dp[i][j] = cand
                    bt[i][j] = ("align", j - k, j, sc)

    # 回溯：从 (n, j*) 取最优，允许末尾剩余参考
    best_j = max(range(m + 1), key=lambda j: dp[n][j] - skip_ref_cost * (m - j) * 0.5)
    pairs: list[list[str]] = [[o, ""] for o in orig_sents]
    scores = [-empty_align_penalty] * n
    i, j = n, best_j
    max_ref_used = 0
    while i > 0 or j > 0:
        step = bt[i][j]
        if step is None:
            break
        if step[0] == "skip_ref":
            j -= 1
            continue
        if step[0] == "empty":
            i -= 1
            continue
        if step[0] == "align":
            j0, j1, sc = step[1], step[2], step[3]
            modern = "".join(ref_sents[j0:j1]).strip()
            pairs[i - 1] = [orig_sents[i - 1], modern]
            scores[i - 1] = sc
            max_ref_used = max(max_ref_used, j1)
            i, j = i - 1, j0
            continue
        break

    low = [idx for idx, sc in enumerate(scores) if sc < 0.35 or not pairs[idx][1]]
    result = AlignResult(pairs, scores, "force_dp", low)
    result.ref_end = max_ref_used  # type: ignore[attr-defined]
    return result


def adjudicate_with_ollama(
    original: str,
    candidates: list[str],
    *,
    model: str = "qwen3:8b",
    ollama_url: str = "http://127.0.0.1:11434/api/chat",
    timeout: int = 120,
) -> tuple[str, float]:
    """本地模型：在候选白话句中为单句原文选连续编号。"""
    import requests

    if not candidates:
        return "", 0.0
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(candidates))
    prompt = f"""你是佛典原文—白话对读员。为下面这一句原文选出对应的白话句子编号。
规则：只使用给出的候选编号；可多选连续编号；无法确认返回空数组。
只输出 JSON：{{"indexes":[编号,...],"confidence":0到1}}

原文：{original}

候选白话：
{numbered}
"""
    response = requests.post(
        ollama_url,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0.0, "num_predict": 200},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json().get("message", {}).get("content", "")
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S)
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return "", 0.0
    data = json.loads(content[start : end + 1])
    indexes = data.get("indexes") or []
    conf = float(data.get("confidence") or 0.5)
    picked = []
    for idx in indexes:
        if isinstance(idx, int) and 1 <= idx <= len(candidates):
            picked.append(candidates[idx - 1])
    text = "".join(picked).strip()
    if text and is_simplified_only(original, text):
        return "", 0.0
    return text, conf


def refine_low_confidence(
    result: AlignResult,
    ref_sents: list[str],
    ref_ranges: list[tuple[int, int]],
    *,
    use_ollama: bool = False,
    model: str = "qwen3:8b",
) -> AlignResult:
    """对低分句在邻近白话窗口内重判。"""
    if not result.low_confidence:
        return result
    pairs = [list(p) for p in result.pairs]
    scores = list(result.scores)
    methods = []
    still_low = []
    for idx in result.low_confidence:
        orig = pairs[idx][0]
        # 邻近窗口：用前后已对齐句估计 ref 区间
        left = idx - 1
        right = idx + 1
        j0, j1 = 0, len(ref_sents)
        while left >= 0 and not pairs[left][1]:
            left -= 1
        while right < len(pairs) and not pairs[right][1]:
            right += 1
        # 粗窗口：整章滑动，取前后句的 ref 邻域
        center = int(idx / max(1, len(pairs) - 1) * max(0, len(ref_sents) - 1))
        w0 = max(0, center - 12)
        w1 = min(len(ref_sents), center + 18)
        window = ref_sents[w0:w1]
        best_text, best_sc = pairs[idx][1], scores[idx]
        # 窗口内穷举 1..3 句（控制复杂度）
        limit = min(len(window), 24)
        for a in range(limit):
            for k in range(1, 4):
                if a + k > len(window):
                    break
                chunk = "".join(window[a : a + k])
                sc = lexical_score(orig, chunk)
                if sc > best_sc:
                    best_sc, best_text = sc, chunk
        method = "force_dp_refine"
        if use_ollama and best_sc < 0.45:
            try:
                text, conf = adjudicate_with_ollama(orig, window, model=model)
                if text:
                    sc = lexical_score(orig, text) + conf
                    if sc > best_sc:
                        best_sc, best_text = sc, text
                        method = "ollama"
            except Exception:
                pass
        pairs[idx][1] = best_text
        scores[idx] = best_sc
        methods.append(method)
        if best_sc < 0.35 or not best_text:
            still_low.append(idx)
    mode = "mixed" if any(m == "ollama" for m in methods) else "force_dp"
    return AlignResult(pairs, scores, mode, still_low)


def _estimate_ref_window(
    segment_text: str,
    ref_sents: list[str],
    ref_bigrams: list[set[str]],
    cursor: int,
) -> tuple[int, int]:
    """从 cursor 起向后找本段参考窗口（单调，不回退到 cursor 之前太远）。"""
    if not ref_sents:
        return 0, 0
    seg_g = _bigrams(segment_text)
    avg_ref = max(12, sum(len(s) for s in ref_sents) // max(1, len(ref_sents)))
    est = max(3, int(len(segment_text) * 2.4 / avg_ref) + 2)
    width = max(4, min(24, est + 2))
    start = max(0, cursor - 2)
    search_end = min(len(ref_sents), cursor + est * 6 + 80)
    best_j, best = start, -1.0
    pos = start
    while pos < search_end:
        end = min(len(ref_sents), pos + width)
        if end <= pos:
            break
        wg: set[str] = set()
        for g in ref_bigrams[pos:end]:
            wg |= g
        sc = len(seg_g & wg) if seg_g and wg else 0
        if sc > best:
            best, best_j = sc, pos
        pos += max(1, width // 2)
    # 单调：窗口从 cursor 附近起，长度封顶避免 DP 爆炸
    w0 = max(0, cursor - 1)
    w1 = min(len(ref_sents), max(best_j + width * 2, cursor + min(60, est * 5 + 15)))
    if w1 - w0 > 80:
        w1 = w0 + 80
    return w0, max(w0 + 1, w1)


def _load_aligned_by_original(book: str = "huayan_t0279") -> dict[int, list[str]]:
    """已人工/模型段级挂好的白话（aligned_layers），按原文段号聚合。"""
    path = Path("data/processed/canon/layers") / f"{book}_aligned_layers.jsonl"
    out: dict[int, list[str]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        oi = row.get("original_segment_index")
        if oi is None and row.get("original_segment_indices"):
            oi = row["original_segment_indices"][0]
        if oi is None:
            continue
        text = (row.get("text") or "").strip()
        if not text or contains_web_junk(text):
            continue
        out.setdefault(int(oi), []).append(text)
    return out


def align_chapter_segments(
    segments: list[dict],
    ref_text: str,
    *,
    use_ollama: bool = False,
    model: str = "qwen3:8b",
    progress: Callable[[str], None] | None = None,
    book: str = "huayan_t0279",
) -> list[dict]:
    """对一章原文段做句句对齐。

    优先：段级已对齐白话（aligned_layers）内部再切句 DP —— 边界准。
    回退：章参考白话上的单调窗口 DP。
    """
    ref_sents = split_classic_sentences(clean_reference_text(ref_text), max_len=140)
    ref_sents = [s for s in ref_sents if not contains_web_junk(s) and not _JUNK_LINE.search(s)]
    ref_bigrams = [_bigrams(s) for s in ref_sents]
    aligned_map = _load_aligned_by_original(book)
    rows: list[dict] = []
    cursor = 0
    if progress:
        progress(
            f"segments={len(segments)} ref_sents={len(ref_sents)} "
            f"aligned_keys={len(aligned_map)} (segment-local DP preferred)"
        )

    for pos, seg in enumerate(segments):
        text = seg.get("text") or ""
        orig_sents = split_classic_sentences(text)
        if not orig_sents:
            continue
        oi = seg.get("segment_index")
        local_refs = aligned_map.get(int(oi)) if oi is not None else None
        if local_refs:
            window = split_classic_sentences("\n".join(local_refs), max_len=140)
            result = force_align_sentences(orig_sents, window, max_ref_span=5)
            result = refine_low_confidence(
                result, window, [], use_ollama=use_ollama, model=model,
            )
            engine_note = "aligned段内DP"
        else:
            w0, w1 = _estimate_ref_window(text, ref_sents, ref_bigrams, cursor)
            window = ref_sents[w0:w1]
            result = force_align_sentences(orig_sents, window, max_ref_span=5)
            result = refine_low_confidence(
                result, window, [], use_ollama=use_ollama, model=model,
            )
            used = int(getattr(result, "ref_end", 0) or 0)
            cursor = min(len(ref_sents), max(cursor + 1, w0 + max(1, used)))
            engine_note = "章窗口DP"

        pairs = result.pairs
        scores = result.scores
        joined = "\n".join(b for _, b in pairs if b)
        low = sum(1 for sc in scores if sc < 0.35)
        avg = sum(scores) / max(1, len(scores))
        rows.append({
            "book": seg.get("book") or book,
            "chapter": seg.get("chapter"),
            "chapter_title": seg.get("chapter_title"),
            "book_chapter_label": seg.get("book_chapter_label"),
            "volume": seg.get("volume"),
            "source_file": seg.get("source_file"),
            "layer": "现代释译",
            "confidence": "low",
            "review_status": "candidate",
            "marker": f"句对齐 · {len(pairs)} 句",
            "translation_source": "洪启嵩译（强制句对齐）",
            "alignment_method": (
                f"句句{engine_note}；mean_score={avg:.2f}；low={low}/{len(pairs)}；"
                f"engine={result.method}"
            ),
            "alignment_status": "候选（句句对齐，待复核）",
            "prompt_version": "force-align-v1",
            "text": joined,
            "pairs": pairs,
            "pair_scores": scores,
            "segment_index": seg.get("segment_index"),
            "original_segment_index": seg.get("segment_index"),
            "original_segment_indices": [seg.get("segment_index")],
        })
        if progress and (pos + 1) % 30 == 0:
            progress(f"  {pos + 1}/{len(segments)} cursor={cursor}")
    return rows
