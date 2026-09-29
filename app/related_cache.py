from __future__ import annotations

import threading
import time
from collections.abc import Callable

from app.config import llm_ready
from app.db import init_db
from app.games.similarity import normalize_word
from app.llm import fetch_refine, fetch_related
from app.paths import DB_PATH

Listener = Callable[[str, dict[str, float]], None]

_mem: dict[str, dict[str, float]] = {}
_mem_lock = threading.Lock()
_queue: list[tuple] = []
_queued: set[tuple] = set()
_queue_lock = threading.Lock()
_worker_started = False
_listener: Listener | None = None


def combine_scores(
    local: float,
    related: float | None,
    *,
    hit_threshold: float = 80.0,
    allow_related_win: bool = False,
) -> float:
    """本地分与模型相关分取较高值。默认把模型分封顶在猜中线之下。"""
    local_score = float(local)
    if related is None:
        return round(local_score, 1)
    related_score = max(0.0, min(100.0, float(related)))
    if not allow_related_win:
        related_score = min(related_score, max(0.0, float(hit_threshold) - 0.5))
    return round(max(local_score, related_score), 1)


def set_listener(fn: Listener | None) -> None:
    global _listener
    _listener = fn


def _emit(secret: str, mapping: dict[str, float]) -> None:
    fn = _listener
    if not fn or not mapping:
        return
    try:
        fn(secret, mapping)
    except Exception:
        return


def get_related(secret: str) -> dict[str, float]:
    key = normalize_word(secret)
    if not key:
        return {}
    with _mem_lock:
        cached = _mem.get(key)
        if cached is not None:
            return dict(cached)
    loaded = _load(key)
    with _mem_lock:
        _mem[key] = loaded
        return dict(loaded)


def lookup_score(secret: str, word: str) -> float | None:
    mapping = get_related(secret)
    return mapping.get(normalize_word(word))


def save_related(secret: str, mapping: dict[str, float]) -> None:
    key = normalize_word(secret)
    if not key or not mapping:
        return
    clean: dict[str, float] = {}
    for word, score in mapping.items():
        word_n = normalize_word(word)
        if not word_n or word_n == key:
            continue
        clean[word_n] = max(0.0, min(100.0, float(score)))
    if not clean:
        return
    import sqlite3

    init_db()
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        conn.executemany(
            """
            INSERT INTO related_cache (secret, word, score, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(secret, word) DO UPDATE SET
                score = excluded.score,
                updated_at = excluded.updated_at
            """,
            [(key, word, score, now) for word, score in clean.items()],
        )
        conn.commit()
    finally:
        conn.close()
    with _mem_lock:
        current = dict(_mem.get(key) or {})
        current.update(clean)
        _mem[key] = current


def _load(secret: str) -> dict[str, float]:
    import sqlite3

    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT word, score FROM related_cache WHERE secret = ?",
            (secret,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {str(row["word"]): float(row["score"]) for row in rows}


def schedule_prefetch(secret: str) -> None:
    key = normalize_word(secret)
    if not key or not llm_ready():
        return
    if get_related(key):
        return
    _enqueue(("prefetch", key))


def schedule_refine(secret: str, word: str) -> None:
    key = normalize_word(secret)
    guess = normalize_word(word)
    if not key or not guess or guess == key or not llm_ready():
        return
    if lookup_score(key, guess) is not None:
        return
    _enqueue(("refine", key, guess))


def _enqueue(job: tuple) -> None:
    with _queue_lock:
        if job in _queued:
            return
        _queued.add(job)
        if job[0] == "prefetch":
            _queue.insert(0, job)
        else:
            _queue.append(job)
    _ensure_worker()


def _ensure_worker() -> None:
    global _worker_started
    with _queue_lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker, name="llm-related", daemon=True).start()


def _pop() -> tuple | None:
    with _queue_lock:
        if not _queue:
            return None
        job = _queue.pop(0)
        _queued.discard(job)
        return job


def _worker() -> None:
    while True:
        job = _pop()
        if job is None:
            time.sleep(0.4)
            continue
        _run_job(job)


def _run_job(job: tuple) -> None:
    try:
        if job[0] == "prefetch":
            mapping = fetch_related(job[1])
        else:
            if lookup_score(job[1], job[2]) is not None:
                return
            mapping = fetch_refine(job[1], [job[2]])
    except Exception:
        return
    if not mapping:
        return
    save_related(job[1], mapping)
    _emit(job[1], get_related(job[1]))


def reset_for_tests() -> None:
    global _worker_started, _listener
    with _queue_lock:
        _queue.clear()
        _queued.clear()
        _worker_started = False
    with _mem_lock:
        _mem.clear()
    _listener = None
