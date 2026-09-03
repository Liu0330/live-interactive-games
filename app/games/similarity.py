from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from pypinyin import Style, lazy_pinyin

from app.paths import RELATED_PATH

_CJK = re.compile(r"[\u4e00-\u9fff]")


def normalize_word(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。！？、；：,.!?;:\"'`~]+", "", text)
    return text


def is_usable_guess(text: str, max_chars: int = 12) -> bool:
    word = normalize_word(text)
    if not word:
        return False
    if len(word) > max_chars:
        return False
    if len(word) == 1 and not _CJK.search(word):
        return False
    return True


def _char_jaccard(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _ngram_overlap(a: str, b: str, n: int = 2) -> float:
    if len(a) < n or len(b) < n:
        return 1.0 if a == b else 0.0
    ga = {a[i : i + n] for i in range(len(a) - n + 1)}
    gb = {b[i : i + n] for i in range(len(b) - n + 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _contain_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        return 0.55 + 0.35 * (len(shorter) / max(len(longer), 1))
    return 0.0


def _pinyin_seq(word: str, style: Style) -> list[str]:
    if not word or not _CJK.search(word):
        return []
    return [p.lower() for p in lazy_pinyin(word, style=style) if p]


def _is_prefix(short: list[str], long: list[str]) -> bool:
    if not short or len(short) >= len(long):
        return False
    return long[: len(short)] == short or long[-len(short) :] == short


def pinyin_similarity(guess: str, secret: str) -> float:
    """0–1。全谐音（含声调）最高；同音不同调次之；无音节重叠为 0。"""
    g, s = normalize_word(guess), normalize_word(secret)
    g_plain = _pinyin_seq(g, Style.NORMAL)
    s_plain = _pinyin_seq(s, Style.NORMAL)
    if not g_plain or not s_plain:
        return 0.0
    g_tone = _pinyin_seq(g, Style.TONE3)
    s_tone = _pinyin_seq(s, Style.TONE3)
    if g_tone and s_tone and g_tone == s_tone:
        return 0.90
    if g_plain == s_plain:
        return 0.78
    if len(g_plain) == len(s_plain):
        matches = sum(a == b for a, b in zip(g_plain, s_plain))
        if matches == 0:
            return 0.0
        tone_matches = sum(a == b for a, b in zip(g_tone, s_tone)) if g_tone and s_tone else 0
        frac = matches / len(g_plain)
        return min(0.62, 0.18 + 0.38 * frac + 0.06 * (tone_matches / len(g_plain)))
    if _is_prefix(g_plain, s_plain) or _is_prefix(s_plain, g_plain):
        shorter = min(len(g_plain), len(s_plain))
        longer = max(len(g_plain), len(s_plain))
        return 0.36 + 0.22 * (shorter / longer)
    inter = set(g_plain) & set(s_plain)
    if not inter:
        return 0.0
    jacc = len(inter) / len(set(g_plain) | set(s_plain))
    if jacc < 0.5:
        return 0.0
    return 0.22 * jacc


@lru_cache(maxsize=1)
def load_related_table(path: str | None = None) -> dict[str, dict[str, float]]:
    target = Path(path) if path else RELATED_PATH
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    table: dict[str, dict[str, float]] = {}
    if not isinstance(raw, dict):
        return table
    for word, related in raw.items():
        key = normalize_word(str(word))
        mapping: dict[str, float] = {}
        if isinstance(related, dict):
            for other, score in related.items():
                try:
                    mapping[normalize_word(str(other))] = float(score)
                except (TypeError, ValueError):
                    continue
        elif isinstance(related, list):
            for item in related:
                mapping[normalize_word(str(item))] = 0.72
        if mapping:
            table[key] = mapping
    return table


def related_boost(guess: str, secret: str, table: dict[str, dict[str, float]] | None = None) -> float:
    table = table if table is not None else load_related_table()
    g, s = normalize_word(guess), normalize_word(secret)
    if s in table and g in table[s]:
        return max(0.0, min(1.0, table[s][g]))
    if g in table and s in table[g]:
        return max(0.0, min(1.0, table[g][s]))
    return 0.0


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def embedding_percent(a: list[float], b: list[float]) -> float:
    """把向量余弦拉成直播榜常用区间：远 20–40，近 65–85，几乎相同接近 100。

    不用 (cos+1)/2，避免无关词堆在 50 附近。
    """
    cos = cosine_similarity(a, b)
    if cos <= 0:
        return round(max(0.0, (cos + 1.0) * 12.0), 1)
    if cos < 0.35:
        pct = 14.0 + (cos / 0.35) * 22.0
    elif cos < 0.55:
        pct = 36.0 + (cos - 0.35) / 0.20 * 26.0
    elif cos < 0.80:
        pct = 62.0 + (cos - 0.55) / 0.25 * 20.0
    else:
        pct = 82.0 + (cos - 0.80) / 0.20 * 18.0
    return round(min(100.0, max(0.0, pct)), 1)


def local_similarity(
    guess: str,
    secret: str,
    table: dict[str, dict[str, float]] | None = None,
) -> float:
    g, s = normalize_word(guess), normalize_word(secret)
    if not g or not s:
        return 0.0
    if g == s:
        return 100.0
    table = table if table is not None else load_related_table()
    jaccard = _char_jaccard(g, s)
    bigram = _ngram_overlap(g, s, 2)
    contain = _contain_score(g, s)
    related = related_boost(g, s, table)
    pinyin = pinyin_similarity(g, s)
    raw = (
        0.20 * jaccard
        + 0.10 * bigram
        + 0.16 * contain
        + 0.22 * related
        + 0.32 * pinyin
    )
    score = 100.0 * raw
    if pinyin >= 0.88:
        score = max(score, 88.0)
    elif pinyin >= 0.75:
        score = max(score, 76.0)
    if related >= 0.85:
        score = max(score, 78.0)
    elif related >= 0.80:
        score = max(score, 74.5)
    elif related >= 0.70:
        score = max(score, 68.0)
    elif related >= 0.50:
        score = max(score, 52.0)
    if contain >= 0.70:
        score = max(score, 72.0)
    return round(max(0.0, min(99.6, score)), 1)


def blend_score(local: float, embed: float | None) -> float:
    if embed is None:
        return local
    return max(local, embed)


def hint_neighbors(
    secret: str,
    pool: list[str] | None = None,
    min_score: float = 55.0,
    table: dict[str, dict[str, float]] | None = None,
) -> list[str]:
    """只从相关词表或高字面/相关邻居出提示，绝不随机抽词库垃圾。"""
    secret_n = normalize_word(secret)
    table = table if table is not None else load_related_table()
    scored: dict[str, float] = {}

    def _add(word: str, value: float) -> None:
        word = normalize_word(word)
        if not word or word == secret_n:
            return
        scored[word] = max(scored.get(word, 0.0), value)

    if secret_n in table:
        for word, value in table[secret_n].items():
            if value >= 0.5:
                _add(word, float(value) * 100.0)
    for word, mapping in table.items():
        if secret_n in mapping and mapping[secret_n] >= 0.5:
            _add(word, float(mapping[secret_n]) * 100.0)

    if pool is None:
        from app.games.wordbank import load_words

        pool = load_words()
    for raw in pool:
        word = normalize_word(raw)
        if not word or word == secret_n:
            continue
        has_signal = (
            related_boost(word, secret_n, table) > 0
            or _char_jaccard(word, secret_n) >= 0.34
            or _contain_score(word, secret_n) > 0
        )
        if not has_signal:
            continue
        sim = local_similarity(word, secret_n, table)
        if sim >= min_score:
            _add(word, sim)

    return [w for w, _ in sorted(scored.items(), key=lambda kv: -kv[1])]


def rank_guesses(entries: list[dict], limit: int = 20) -> list[dict]:
    """按相似度降序，同一词语只保留最高分（同分先到）。"""
    best: dict[str, dict] = {}
    for item in entries:
        word = normalize_word(item.get("word") or item.get("content") or "")
        if not word:
            continue
        score = float(item.get("score") or 0)
        prev = best.get(word)
        if prev is None or score > float(prev["score"]):
            row = dict(item)
            row["word"] = word
            row["score"] = score
            best[word] = row
    ranked = sorted(
        best.values(),
        key=lambda x: (-float(x["score"]), int(x.get("seq") or 10**9)),
    )
    for i, row in enumerate(ranked[:limit], start=1):
        row["place"] = i
    return ranked[:limit]
