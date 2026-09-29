from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable

from app.config import minimax_ready, minimax_settings
from app.db import init_db
from app.minimax import embed_texts
from app.paths import DB_PATH

Listener = Callable[[list[str]], None]

_mem: dict[tuple[str, str, str], list[float]] = {}
_mem_lock = threading.Lock()
_queue: list[tuple[str, str]] = []
_queued: set[tuple[str, str]] = set()
_queue_lock = threading.Lock()
_worker_started = False
_listener: Listener | None = None
_fail_until: dict[tuple[str, str, str], float] = {}


def set_listener(fn: Listener | None) -> None:
    global _listener
    _listener = fn


def _emit(texts: list[str]) -> None:
    fn = _listener
    if not fn or not texts:
        return
    try:
        fn(texts)
    except Exception:
        return


def _cache_key(kind: str, text: str) -> tuple[str, str, str] | None:
    from app.games.similarity import normalize_word

    word = normalize_word(text)
    if not word:
        return None
    model = minimax_settings()["embed_model"]
    side = "query" if kind == "query" else "db"
    return (model, side, word)


def get_vector(kind: str, text: str) -> list[float] | None:
    key = _cache_key(kind, text)
    if key is None:
        return None
    with _mem_lock:
        cached = _mem.get(key)
        if cached is not None:
            return list(cached)
    loaded = _load(key)
    if loaded is None:
        return None
    with _mem_lock:
        _mem[key] = loaded
        return list(loaded)


def lookup_percent(secret: str, guess: str) -> float | None:
    from app.games.similarity import embedding_percent

    doc = get_vector("db", secret)
    query = get_vector("query", guess)
    if not doc or not query:
        return None
    return embedding_percent(doc, query)


def save_vectors(kind: str, pairs: list[tuple[str, list[float]]]) -> list[str]:
    saved: list[str] = []
    rows = []
    now = int(time.time())
    model = minimax_settings()["embed_model"]
    side = "query" if kind == "query" else "db"
    clean: list[tuple[tuple[str, str, str], list[float], str]] = []
    for text, vector in pairs:
        key = _cache_key(side, text)
        if key is None or not vector:
            continue
        clean.append((key, [float(value) for value in vector], key[2]))
        rows.append((model, side, key[2], json.dumps(vector), now))
        saved.append(key[2])
    if not rows:
        return []
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        conn.executemany(
            """
            INSERT INTO embed_cache (model, kind, text, vector, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(model, kind, text) DO UPDATE SET
                vector = excluded.vector,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    with _mem_lock:
        for key, vector, _word in clean:
            _mem[key] = vector
            _fail_until.pop(key, None)
    return saved


def _load(key: tuple[str, str, str]) -> list[float] | None:
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        row = conn.execute(
            "SELECT vector FROM embed_cache WHERE model = ? AND kind = ? AND text = ?",
            key,
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, list) or not data:
        return None
    return [float(value) for value in data]


def schedule_secret(secret: str) -> None:
    _schedule("db", secret)


def schedule_guess(secret: str, guess: str) -> None:
    _schedule("db", secret)
    _schedule("query", guess)


def _schedule(kind: str, text: str) -> None:
    key = _cache_key(kind, text)
    if key is None or not minimax_ready():
        return
    if get_vector(kind, text):
        return
    if _fail_until.get(key, 0) > time.time():
        return
    job = (key[1], key[2])
    with _queue_lock:
        if job in _queued:
            return
        _queued.add(job)
        _queue.append(job)
    _ensure_worker()


def _ensure_worker() -> None:
    global _worker_started
    with _queue_lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker, name="minimax-embed", daemon=True).start()


def _pop() -> tuple[str, str] | None:
    with _queue_lock:
        if not _queue:
            return None
        job = _queue.pop(0)
        _queued.discard(job)
        return job


def _peek() -> tuple[str, str] | None:
    with _queue_lock:
        if not _queue:
            return None
        return _queue[0]


def _worker() -> None:
    while True:
        job = _pop()
        if job is None:
            time.sleep(0.25)
            continue
        kind = job[0]
        batch = [job]
        deadline = time.time() + 0.12
        while len(batch) < 16 and time.time() < deadline:
            nxt = _peek()
            if nxt is None or nxt[0] != kind:
                time.sleep(0.02)
                continue
            taken = _pop()
            if taken is not None:
                batch.append(taken)
        _run_batch(kind, [item[1] for item in batch])


def _run_batch(kind: str, texts: list[str]) -> None:
    unique: list[str] = []
    for text in texts:
        if text and text not in unique:
            unique.append(text)
    if not unique:
        return
    try:
        vectors = embed_texts(unique, kind)
    except Exception:
        vectors = None
    if not vectors or len(vectors) != len(unique):
        until = time.time() + 20
        for text in unique:
            key = _cache_key(kind, text)
            if key is not None:
                _fail_until[key] = until
        return
    saved = save_vectors(kind, list(zip(unique, vectors)))
    _emit(saved)


def reset_for_tests() -> None:
    global _worker_started, _listener
    with _queue_lock:
        _queue.clear()
        _queued.clear()
        _worker_started = False
    with _mem_lock:
        _mem.clear()
    _fail_until.clear()
    _listener = None
