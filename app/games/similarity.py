from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

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
    cos = cosine_similarity(a, b)
    # 将 [-1, 1] 映射到更适合展示的 0-100，同向相近时拉开区分度
    mapped = (cos + 1) / 2
    return round(max(0.0, min(100.0, mapped * 100)), 1)


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
    length = 1 - abs(len(g) - len(s)) / max(len(g), len(s), 1)
    raw = (
        0.34 * jaccard
        + 0.18 * bigram
        + 0.18 * contain
        + 0.22 * related
        + 0.08 * length
    )
    score = 100.0 * raw
    if related >= 0.85:
        score = max(score, 78.0)
    elif related >= 0.7:
        score = max(score, 64.0)
    elif related >= 0.5:
        score = max(score, 48.0)
    return round(max(0.0, min(99.6, score)), 1)


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
