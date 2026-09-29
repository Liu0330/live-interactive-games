from __future__ import annotations

import re
import sqlite3
import threading
import time
from functools import lru_cache

from pypinyin import Style, lazy_pinyin

from app.db import init_db
from app.games.similarity import normalize_word
from app.paths import DB_PATH, IDIOMS_PATH

_FOUR = re.compile(r"^[\u4e00-\u9fff]{4}$")
_lock = threading.Lock()
_extra_mem: set[str] | None = None


def clean_idiom(text: str) -> str:
    word = normalize_word(text)
    return word if _FOUR.match(word) else ""


@lru_cache(maxsize=1)
def bundled_idioms() -> tuple[str, ...]:
    if not IDIOMS_PATH.exists():
        return ()
    seen: list[str] = []
    found: set[str] = set()
    for line in IDIOMS_PATH.read_text(encoding="utf-8").splitlines():
        word = clean_idiom(line)
        if word and word not in found:
            found.add(word)
            seen.append(word)
    return tuple(seen)


def extra_idioms() -> set[str]:
    global _extra_mem
    with _lock:
        if _extra_mem is not None:
            return set(_extra_mem)
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        rows = conn.execute("SELECT idiom FROM extra_idioms").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    found = {clean_idiom(str(row[0])) for row in rows}
    found.discard("")
    with _lock:
        _extra_mem = set(found)
    return set(found)


def all_idioms() -> set[str]:
    from app.banks import hidden_keys, idiom_labels

    found = set(bundled_idioms()) | extra_idioms() | idiom_labels()
    hidden = hidden_keys("idiom")
    return {word for word in found if word not in hidden}


def remember_idiom(idiom: str) -> None:
    global _extra_mem
    word = clean_idiom(idiom)
    if not word:
        return
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        conn.execute(
            "INSERT INTO extra_idioms (idiom, created_at) VALUES (?, ?) ON CONFLICT(idiom) DO NOTHING",
            (word, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()
    with _lock:
        if _extra_mem is None:
            _extra_mem = set()
        _extra_mem.add(word)
    from app.banks import upsert_idiom

    upsert_idiom(word)


def char_pinyin(ch: str) -> str:
    parts = lazy_pinyin(ch or "", style=Style.NORMAL)
    return "".join(parts).lower()


def links(previous: str, nxt: str, allow_pinyin: bool) -> bool:
    prev = clean_idiom(previous) or normalize_word(previous)
    word = clean_idiom(nxt) or normalize_word(nxt)
    if len(prev) < 1 or len(word) < 1:
        return False
    if word[0] == prev[-1]:
        return True
    if not allow_pinyin:
        return False
    left = char_pinyin(prev[-1])
    right = char_pinyin(word[0])
    return bool(left) and left == right


def continuations(head: str, allow_pinyin: bool, used: set[str] | None = None) -> list[str]:
    banned = used or set()
    head_n = normalize_word(head)
    exact = []
    phonetic = []
    for word in sorted(all_idioms()):
        if word in banned or word == head_n:
            continue
        if word[0] == head_n[-1]:
            exact.append(word)
        elif allow_pinyin and links(head_n, word, True):
            phonetic.append(word)
    return exact + phonetic


def reset_idiom_cache() -> None:
    global _extra_mem
    bundled_idioms.cache_clear()
    with _lock:
        _extra_mem = None
