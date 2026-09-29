"""升级必须保住当前版本已经写进 SQLite 的数据。"""

import sqlite3
from pathlib import Path

from app.db import add_points, get_user, init_db, leaderboard
from app.migrate import backup_dir_for, migrate_database


CURRENT_SCHEMA = """
CREATE TABLE scores (
    user_id TEXT PRIMARY KEY,
    nickname TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    best_streak INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE score_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    nickname TEXT NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_score_events_time ON score_events(created_at);
CREATE TABLE room_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE related_cache (
    secret TEXT NOT NULL,
    word TEXT NOT NULL,
    score REAL NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (secret, word)
);
CREATE TABLE embed_cache (
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    vector TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (model, kind, text)
);
CREATE TABLE extra_idioms (
    idiom TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);
CREATE TABLE emoji_puzzles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    emojis TEXT NOT NULL,
    answer TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    hint TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE TABLE emoji_hidden (
    puzzle_key TEXT PRIMARY KEY
);
CREATE TABLE words (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL
);
"""


def _seed_current(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(CURRENT_SCHEMA)
        conn.executemany(
            "INSERT INTO scores (user_id, nickname, points, streak, best_streak) VALUES (?, ?, ?, ?, ?)",
            [
                ("dy-1", "路人甲", 120, 3, 7),
                ("dy-2", "路人乙", 40, 0, 1),
                (" dy-9", "空格号", 100, 2, 5),
                ("dy-9", "正号", 50, 1, 4),
            ],
        )
        conn.execute(
            "INSERT INTO score_events (user_id, nickname, delta, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            ("dy-1", "路人甲", 30, "win", 1_700_000_000),
        )
        conn.execute("INSERT INTO room_meta (key, value) VALUES (?, ?)", ("active_game", "semantic"))
        conn.execute(
            "INSERT INTO related_cache (secret, word, score, updated_at) VALUES (?, ?, ?, ?)",
            ("春卷", "饺子", 71.5, 10),
        )
        conn.execute(
            "INSERT INTO embed_cache (model, kind, text, vector, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("embo-01", "db", "春卷", "[0.1,0.2]", 11),
        )
        conn.execute("INSERT INTO extra_idioms (idiom, created_at) VALUES (?, ?)", ("一心一意", 12))
        conn.execute(
            """
            INSERT INTO emoji_puzzles (category, emojis, answer, aliases, hint, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("idiom", "🦊🐯", "狐假虎威", "[]", "狐狸", 13),
        )
        conn.execute("INSERT INTO emoji_hidden (puzzle_key) VALUES (?)", ("b1",))
        conn.executemany("INSERT INTO words (id, text) VALUES (?, ?)", [(1, "春卷"), (2, "汤圆")])
        conn.commit()
    finally:
        conn.close()


def _dump(path: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            name: conn.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for name in tables
        }
    finally:
        conn.close()


def _preserved(before: dict, after: dict) -> None:
    for name, rows in before.items():
        assert name in after, name
        assert after[name] == rows


def test_current_schema_keeps_history_and_backs_up_once(tmp_path, monkeypatch):
    db_path = tmp_path / "leaderboard.db"
    _seed_current(db_path)
    before = _dump(db_path)
    monkeypatch.setattr("app.db.DB_PATH", db_path)

    first = init_db()

    after = _dump(db_path)
    _preserved(before, after)
    assert "global_accounts" in after
    assert "account_sources" in after
    accounts = {row[0]: row for row in after["global_accounts"]}
    assert accounts["dy-1"][1:] == ("路人甲", 120, 3, 7)
    assert accounts["dy-2"][1:] == ("路人乙", 40, 0, 1)
    assert accounts["dy-9"][1:] == ("正号", 150, 2, 5)
    sources = {row[0]: row[1] for row in after["account_sources"]}
    assert sources == {"dy-1": "dy-1", "dy-2": "dy-2", " dy-9": "dy-9", "dy-9": "dy-9"}
    assert after["words"] == [(1, "春卷"), (2, "汤圆")]

    backup = Path(first["backup"])
    assert backup.is_file()
    assert backup.parent == backup_dir_for(db_path)
    assert backup.name.startswith("leaderboard-")
    saved = _dump(backup)
    assert "global_accounts" not in saved
    assert "schema_migrations" not in saved
    _preserved(before, saved)

    assert get_user("dy-1")["points"] == 120
    assert get_user(" dy-9")["points"] == 150
    board = {row["user_id"]: row["points"] for row in leaderboard()}
    assert board["dy-9"] == 150
    assert board["dy-1"] == 120

    second = migrate_database(db_path)
    assert second["applied"] == []
    assert second["backup"] is None
    assert list(backup_dir_for(db_path).glob("*.db")) == [backup]
    assert _dump(db_path)["global_accounts"] == after["global_accounts"]

    assert add_points(" dy-9", "空格号", 10) == 110
    merged = get_user("dy-9")
    assert merged["points"] == 160
    still = _dump(db_path)
    score_points = {row[0]: row[2] for row in still["scores"]}
    assert score_points[" dy-9"] == 110
    assert score_points["dy-9"] == 50
    assert score_points["dy-1"] == 120
    conn = sqlite3.connect(db_path)
    try:
        idiom = conn.execute(
            "SELECT source FROM bank_items WHERE kind = 'idiom' AND dedupe_key = '一心一意'"
        ).fetchone()
        puzzle = conn.execute(
            "SELECT COUNT(*) FROM bank_items WHERE kind = 'puzzle' AND dedupe_key = 'idiom:狐假虎威'"
        ).fetchone()
        bank_count = conn.execute("SELECT COUNT(*) FROM bank_items").fetchone()[0]
        assert conn.execute("SELECT text FROM words WHERE id = 1").fetchone()[0] == "春卷"
    finally:
        conn.close()
    assert idiom[0] == "legacy"
    assert puzzle[0] == 1
    again = migrate_database(db_path)
    assert again["applied"] == []
    assert again["backup"] is None
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM bank_items").fetchone()[0] == bank_count
        assert conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'words'").fetchone()
    finally:
        conn.close()


def test_original_scores_table_gains_columns_without_losing_points(tmp_path):
    db_path = tmp_path / "leaderboard.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE scores (
            user_id TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("INSERT INTO scores (user_id, nickname, points) VALUES (?, ?, ?)", ("u1", "甲", 40))
    conn.execute("CREATE TABLE custom_notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    conn.execute("INSERT INTO custom_notes (id, body) VALUES (1, '主播备注')")
    conn.commit()
    conn.close()

    result = migrate_database(db_path)
    assert result["backup"]
    dumped = _dump(db_path)
    assert dumped["scores"] == [("u1", "甲", 40, 0, 0)]
    assert dumped["custom_notes"] == [(1, "主播备注")]
    assert dumped["global_accounts"] == [("u1", "甲", 40, 0, 0)]
    again = migrate_database(db_path)
    assert again["applied"] == []
    assert again["backup"] is None
    assert _dump(db_path)["global_accounts"] == [("u1", "甲", 40, 0, 0)]


def test_fresh_database_has_no_backup(tmp_path):
    db_path = tmp_path / "nested" / "leaderboard.db"
    result = migrate_database(db_path)
    assert result["backup"] is None
    assert result["applied"]
    assert db_path.is_file()
    assert not backup_dir_for(db_path).exists()


def test_migration_source_never_drops_tables():
    source = Path("app/migrate.py").read_text(encoding="utf-8").lower()
    assert "drop " not in source
    assert "delete from" not in source
