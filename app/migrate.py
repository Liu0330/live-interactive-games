"""把已有的直播数据库升级到当前结构。

只追加表和字段，不删除、不重建已有表。真正改库之前先做一份带时间戳的备份。
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

from app.paths import DB_PATH, ensure_user_dirs

MIGRATIONS: list[tuple[str, str]] = [
    ("001_core_scores", "确保积分表存在"),
    ("002_score_progress", "补上连击字段"),
    ("003_events_and_room", "补上积分流水和房间状态"),
    ("004_model_cache", "补上模型和向量缓存"),
    ("005_game_banks", "补上成语和看图猜题库"),
    ("006_global_accounts", "把已有积分归入全局账号"),
    ("007_content_banks", "补上可扩充的题库和观众出题"),
    ("008_api_usage", "补上接口用量记录"),
]


def backup_dir_for(db_path: Path) -> Path:
    return db_path.parent / "backups"


def migrate_database(db_path: Path | None = None) -> dict:
    path = Path(db_path or DB_PATH)
    ensure_user_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = _pending(path)
    backup: Path | None = None
    if pending and path.exists() and path.stat().st_size > 0:
        backup = backup_database(path)
    if pending:
        _apply(path, pending)
    return {
        "backup": str(backup) if backup else None,
        "applied": list(pending),
    }


def backup_database(db_path: Path) -> Path:
    """用 SQLite 备份接口复制一份一致的数据库文件。"""
    folder = backup_dir_for(db_path)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = folder / f"leaderboard-{stamp}.db"
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return dest


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _pending(path: Path) -> list[str]:
    if not path.exists():
        return [version for version, _label in MIGRATIONS]
    conn = _connect(path)
    try:
        done = _applied(conn)
    finally:
        conn.close()
    return [version for version, _label in MIGRATIONS if version not in done]


def _applied(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row["version"]) for row in rows}


def _apply(path: Path, pending: list[str]) -> None:
    steps = {version: fn for version, fn in _STEPS}
    conn = _connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
            """
        )
        for version in pending:
            steps[version](conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?) ON CONFLICT(version) DO NOTHING",
                (version, int(time.time())),
            )
            conn.commit()
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row[1]) for row in rows}


def _step_core_scores(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scores (
            user_id TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def _step_score_progress(conn: sqlite3.Connection) -> None:
    cols = _columns(conn, "scores")
    if "streak" not in cols:
        conn.execute("ALTER TABLE scores ADD COLUMN streak INTEGER NOT NULL DEFAULT 0")
    if "best_streak" not in cols:
        conn.execute("ALTER TABLE scores ADD COLUMN best_streak INTEGER NOT NULL DEFAULT 0")


def _step_events_and_room(conn: sqlite3.Connection) -> None:
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_score_events_time ON score_events(created_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS room_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _step_model_cache(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embed_cache (
            model TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            vector TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (model, kind, text)
        )
        """
    )


def _step_game_banks(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extra_idioms (
            idiom TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS emoji_puzzles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            emojis TEXT NOT NULL,
            answer TEXT NOT NULL,
            aliases TEXT NOT NULL DEFAULT '[]',
            hint TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS emoji_hidden (
            puzzle_key TEXT PRIMARY KEY
        )
        """
    )


def _account_key(user_id: str, nickname: str = "") -> str:
    uid = (user_id or "").strip()
    nick = (nickname or "").strip()
    return uid or nick or "观众"


def _step_global_accounts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS global_accounts (
            account_id TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_sources (
            legacy_user_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL
        )
        """
    )
    cols = _columns(conn, "scores")
    has_streak = "streak" in cols and "best_streak" in cols
    streak_sql = ", streak, best_streak" if has_streak else ""
    rows = conn.execute(f"SELECT user_id, nickname, points{streak_sql} FROM scores").fetchall()
    mapped = {str(row["legacy_user_id"]) for row in conn.execute("SELECT legacy_user_id FROM account_sources")}
    for row in rows:
        legacy = str(row["user_id"])
        if legacy in mapped:
            continue
        nickname = str(row["nickname"] or "观众")
        account = _account_key(legacy, nickname)
        points = int(row["points"] or 0)
        streak = int(row["streak"] or 0) if has_streak else 0
        best = int(row["best_streak"] or 0) if has_streak else 0
        existing = conn.execute(
            "SELECT points, streak, best_streak FROM global_accounts WHERE account_id = ?",
            (account,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE global_accounts
                SET nickname = ?, points = points + ?, streak = MAX(streak, ?), best_streak = MAX(best_streak, ?)
                WHERE account_id = ?
                """,
                (nickname, points, streak, best, account),
            )
        else:
            conn.execute(
                """
                INSERT INTO global_accounts (account_id, nickname, points, streak, best_streak)
                VALUES (?, ?, ?, ?, ?)
                """,
                (account, nickname, points, streak, best),
            )
        conn.execute(
            "INSERT INTO account_sources (legacy_user_id, account_id) VALUES (?, ?)",
            (legacy, account),
        )
        mapped.add(legacy)


def _step_content_banks(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bank_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL DEFAULT 'manual',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(kind, dedupe_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bank_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            nickname TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bank_hidden (
            kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            PRIMARY KEY (kind, dedupe_key)
        )
        """
    )
    from app.banks import copy_legacy_rows

    copy_legacy_rows(conn)


def _step_api_usage(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            provider TEXT NOT NULL,
            kind TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            characters INTEGER,
            vectors INTEGER,
            ok INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage (created_at)")


_STEPS = [
    ("001_core_scores", _step_core_scores),
    ("002_score_progress", _step_score_progress),
    ("003_events_and_room", _step_events_and_room),
    ("004_model_cache", _step_model_cache),
    ("005_game_banks", _step_game_banks),
    ("006_global_accounts", _step_global_accounts),
    ("007_content_banks", _step_content_banks),
    ("008_api_usage", _step_api_usage),
]
