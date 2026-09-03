from __future__ import annotations

import random
import re
from pathlib import Path

from app.paths import COMMON_GUESSES_PATH, WORDPOOL_PATH

HEADER = "# 语义猜词 谜底词库"


def classify_word(word: str) -> str:
    n = len(word)
    if n >= 4:
        return "四字词语"
    if n == 3:
        return "名词"
    if n == 2:
        return "名词"
    return "词语"


def answer_len_label(word: str) -> str:
    return f"{len(word)}字"


def load_words(path: Path | None = None) -> list[str]:
    target = path or WORDPOOL_PATH
    if not target.exists():
        return []
    words: list[str] = []
    seen: set[str] = set()
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word = line.split("|", 1)[0].strip()
        if word and word not in seen:
            seen.add(word)
            words.append(word)
    return words


def save_words(words: list[str], path: Path | None = None) -> list[str]:
    target = path or WORDPOOL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    unique: list[str] = []
    seen: set[str] = set()
    for word in words:
        word = word.strip()
        if not word or word.startswith("#") or word in seen:
            continue
        seen.add(word)
        unique.append(word)
    body = HEADER + "\n" + "\n".join(unique) + "\n"
    target.write_text(body, encoding="utf-8")
    return unique


def add_words(words: list[str], overwrite: bool = False, path: Path | None = None) -> list[str]:
    if overwrite:
        return save_words(words, path)
    current = load_words(path)
    return save_words(current + words, path)


def remove_word(word: str, path: Path | None = None) -> list[str]:
    words = [w for w in load_words(path) if w != word]
    return save_words(words, path)


def pick_word(specified: str = "", answer_length: int = 0, path: Path | None = None) -> str:
    specified = (specified or "").strip()
    if specified:
        return specified
    words = load_words(path)
    if answer_length:
        filtered = [w for w in words if len(w) == answer_length]
        if filtered:
            words = filtered
    if not words:
        return "天空"
    return random.choice(words)


def load_common_guesses(path: Path | None = None) -> list[str]:
    target = path or COMMON_GUESSES_PATH
    if target.exists():
        words = load_words(target)
        if words:
            return words
    return load_words()


_SAFE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9]{1,8}$")


def sanitize_generated(words: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in words:
        word = (raw or "").strip()
        if not _SAFE.match(word):
            continue
        if word in seen:
            continue
        seen.add(word)
        out.append(word)
    return out
