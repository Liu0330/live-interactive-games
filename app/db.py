from __future__ import annotations

import sqlite3
from threading import Lock

from app.paths import DB_PATH, ensure_user_dirs
from app.ranks import rank_icon, rank_title

_lock = Lock()


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


def get_points(user_id: str) -> int:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT points FROM scores WHERE user_id = ?", (user_id,)
            ).fetchone()
            return int(row["points"]) if row else 0
        finally:
            conn.close()


def leaderboard(limit: int = 20, rank_names: list[str] | None = None, per_sub: int = 180) -> list[dict]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT user_id, nickname, points FROM scores ORDER BY points DESC, nickname ASC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    out = []
    for i, row in enumerate(rows, start=1):
        title = rank_title(int(row["points"]), rank_names, per_sub)
        out.append(
            {
                "place": i,
                "user_id": row["user_id"],
                "nickname": row["nickname"],
                "points": int(row["points"]),
                "rank_name": title,
                "icon": rank_icon(title),
            }
        )
    return out


def clear_leaderboard() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM scores")
            conn.commit()
        finally:
            conn.close()
