from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.paths import DB_PATH, ensure_user_dirs
from app.ranks import rank_icon, rank_title

_lock = __import__("threading").Lock()
TZ = ZoneInfo("Asia/Shanghai")


def _connect() -> sqlite3.Connection:
    ensure_user_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scores (
                    user_id TEXT PRIMARY KEY,
                    nickname TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(scores)")}
            if "streak" not in cols:
                conn.execute("ALTER TABLE scores ADD COLUMN streak INTEGER NOT NULL DEFAULT 0")
            if "best_streak" not in cols:
                conn.execute("ALTER TABLE scores ADD COLUMN best_streak INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS score_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    delta INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_score_events_time ON score_events(created_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS room_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS related_cache (
                    secret TEXT NOT NULL,
                    word TEXT NOT NULL,
                    score REAL NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (secret, word)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def add_points(user_id: str, nickname: str, delta: int) -> int:
    if delta == 0:
        return get_points(user_id)
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT points FROM scores WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                points = int(row["points"]) + int(delta)
                conn.execute(
                    "UPDATE scores SET nickname = ?, points = ? WHERE user_id = ?",
                    (nickname, points, user_id),
                )
            else:
                points = int(delta)
                conn.execute(
                    "INSERT INTO scores (user_id, nickname, points) VALUES (?, ?, ?)",
                    (user_id, nickname, points),
                )
            conn.commit()
            return points
        finally:
            conn.close()


def add_score_event(user_id: str, nickname: str, delta: int, reason: str, created_at: int | None = None) -> None:
    if not delta:
        return
    when = int(created_at if created_at is not None else time.time())
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO score_events (user_id, nickname, delta, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, nickname, int(delta), reason or "points", when),
            )
            conn.commit()
        finally:
            conn.close()


def get_points(user_id: str) -> int:
    return int(get_user(user_id).get("points") or 0)


def get_user(user_id: str) -> dict:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT nickname, points, streak, best_streak FROM scores WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return {"exists": False, "nickname": "", "points": 0, "streak": 0, "best_streak": 0}
    return {
        "exists": True,
        "nickname": row["nickname"],
        "points": int(row["points"]),
        "streak": int(row["streak"] or 0),
        "best_streak": int(row["best_streak"] or 0),
    }


def set_streak(user_id: str, nickname: str, streak: int) -> None:
    streak = max(0, int(streak))
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT best_streak FROM scores WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                best = max(int(row["best_streak"] or 0), streak)
                conn.execute(
                    "UPDATE scores SET nickname = ?, streak = ?, best_streak = ? WHERE user_id = ?",
                    (nickname, streak, best, user_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO scores (user_id, nickname, points, streak, best_streak)
                    VALUES (?, ?, 0, ?, ?)
                    """,
                    (user_id, nickname, streak, streak),
                )
            conn.commit()
        finally:
            conn.close()


def period_start(period: str, now: datetime | None = None) -> int:
    current = now or datetime.now(TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ)
    else:
        current = current.astimezone(TZ)
    if period == "day":
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        monday = current - timedelta(days=current.weekday())
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        return 0
    return int(start.timestamp())


def _row_payload(place: int, user_id: str, nickname: str, points: int, total: int, streak: int, rank_names, per_sub) -> dict:
    title = rank_title(int(total), rank_names, per_sub)
    return {
        "place": place,
        "user_id": user_id,
        "nickname": nickname,
        "points": int(points),
        "total_points": int(total),
        "streak": int(streak),
        "rank_name": title,
        "icon": rank_icon(title),
    }


def leaderboard(limit: int = 20, rank_names: list[str] | None = None, per_sub: int = 180) -> list[dict]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT user_id, nickname, points, streak
                FROM scores
                ORDER BY points DESC, nickname ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    out = []
    for i, row in enumerate(rows, start=1):
        points = int(row["points"])
        out.append(
            _row_payload(
                i,
                row["user_id"],
                row["nickname"],
                points,
                points,
                int(row["streak"] or 0),
                rank_names,
                per_sub,
            )
        )
    return out


def leaderboard_period(
    period: str,
    limit: int = 8,
    rank_names: list[str] | None = None,
    per_sub: int = 180,
    now: datetime | None = None,
) -> list[dict]:
    start = period_start(period, now)
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT e.user_id AS user_id,
                       MAX(COALESCE(s.nickname, e.nickname)) AS nickname,
                       SUM(e.delta) AS points,
                       MAX(COALESCE(s.points, 0)) AS total_points,
                       MAX(COALESCE(s.streak, 0)) AS streak
                FROM score_events e
                LEFT JOIN scores s ON s.user_id = e.user_id
                WHERE e.created_at >= ?
                GROUP BY e.user_id
                ORDER BY points DESC, nickname ASC
                LIMIT ?
                """,
                (start, limit),
            ).fetchall()
        finally:
            conn.close()
    out = []
    for i, row in enumerate(rows, start=1):
        out.append(
            _row_payload(
                i,
                row["user_id"],
                row["nickname"],
                int(row["points"] or 0),
                int(row["total_points"] or 0),
                int(row["streak"] or 0),
                rank_names,
                per_sub,
            )
        )
    return out


def meta_get(key: str, default: str | None = None) -> str | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT value FROM room_meta WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
    if not row:
        return default
    return str(row["value"])


def meta_set(key: str, value: str) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO room_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            conn.commit()
        finally:
            conn.close()


def clear_leaderboard() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM scores")
            conn.execute("DELETE FROM score_events")
            conn.commit()
        finally:
            conn.close()
