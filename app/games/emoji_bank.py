from __future__ import annotations

import json
import sqlite3
import time

from app.db import init_db
from app.games.similarity import normalize_word
from app.paths import DB_PATH, EMOJI_PUZZLES_PATH

_BANNED = ("习近平", "共产党", "法轮", "色情", "裸体", "赌博")


def _row_puzzle(key: str, category: str, emojis: str, answer: str, aliases: list, hint: str, source: str) -> dict:
    return {
        "id": key,
        "category": category,
        "emojis": emojis,
        "answer": answer,
        "aliases": aliases,
        "hint": hint,
        "source": source,
    }


def bundled_puzzles() -> list[dict]:
    if not EMOJI_PUZZLES_PATH.exists():
        return []
    try:
        data = json.loads(EMOJI_PUZZLES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        puzzle = sanitize_puzzle(item, source="bundle")
        if puzzle:
            puzzle["id"] = str(item.get("id") or puzzle["answer"])
            out.append(puzzle)
    return out


def hidden_keys() -> set[str]:
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        rows = conn.execute("SELECT puzzle_key FROM emoji_hidden").fetchall()
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def stored_puzzles() -> list[dict]:
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, category, emojis, answer, aliases, hint FROM emoji_puzzles ORDER BY id DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    out = []
    for row in rows:
        try:
            aliases = json.loads(row["aliases"] or "[]")
        except json.JSONDecodeError:
            aliases = []
        puzzle = _row_puzzle(
            f"db:{row['id']}",
            str(row["category"]),
            str(row["emojis"]),
            str(row["answer"]),
            aliases if isinstance(aliases, list) else [],
            str(row["hint"] or ""),
            "generated",
        )
        out.append(puzzle)
    return out


def all_puzzles() -> list[dict]:
    hidden = hidden_keys()
    merged = [item for item in bundled_puzzles() if item["id"] not in hidden]
    merged.extend(item for item in stored_puzzles() if item["id"] not in hidden)
    return merged


def sanitize_puzzle(item: dict, source: str = "generated") -> dict | None:
    category = str(item.get("category") or "").strip()
    if category in {"成语", "idioms"}:
        category = "idiom"
    if category in {"歌", "歌名", "songs"}:
        category = "song"
    if category not in {"idiom", "song"}:
        return None
    emojis = str(item.get("emojis") or "").strip()
    answer = normalize_word(str(item.get("answer") or ""))
    if not emojis or not answer:
        return None
    if not any(ord(ch) > 127 for ch in emojis):
        return None
    if category == "idiom" and not (4 <= len(answer) <= 8):
        return None
    if category == "song" and not (2 <= len(answer) <= 16):
        return None
    blob = emojis + answer + str(item.get("hint") or "")
    if any(word in blob for word in _BANNED):
        return None
    aliases = []
    for alias in item.get("aliases") or []:
        cleaned = normalize_word(str(alias))
        if cleaned and cleaned != answer:
            aliases.append(cleaned)
    hint = str(item.get("hint") or "").strip()[:24]
    return _row_puzzle("", category, emojis, answer, aliases[:4], hint, source)


def save_generated(items: list[dict]) -> list[dict]:
    existing = {item["answer"] for item in all_puzzles()}
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    saved = []
    now = int(time.time())
    try:
        for raw in items:
            puzzle = sanitize_puzzle(raw, source="generated")
            if not puzzle or puzzle["answer"] in existing:
                continue
            cur = conn.execute(
                """
                INSERT INTO emoji_puzzles (category, emojis, answer, aliases, hint, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    puzzle["category"],
                    puzzle["emojis"],
                    puzzle["answer"],
                    json.dumps(puzzle["aliases"], ensure_ascii=False),
                    puzzle["hint"],
                    now,
                ),
            )
            puzzle["id"] = f"db:{cur.lastrowid}"
            existing.add(puzzle["answer"])
            saved.append(puzzle)
        conn.commit()
    finally:
        conn.close()
    return saved


def delete_puzzle(puzzle_id: str) -> None:
    key = (puzzle_id or "").strip()
    if not key:
        return
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        if key.startswith("db:"):
            try:
                row_id = int(key.split(":", 1)[1])
            except ValueError:
                return
            conn.execute("DELETE FROM emoji_puzzles WHERE id = ?", (row_id,))
        else:
            conn.execute(
                "INSERT INTO emoji_hidden (puzzle_key) VALUES (?) ON CONFLICT(puzzle_key) DO NOTHING",
                (key,),
            )
        conn.commit()
    finally:
        conn.close()


def generate_with_model(count: int, category: str) -> list[dict]:
    from app.config import chat_ready
    from app.llm import ChatError, chat_json

    if not chat_ready():
        raise ChatError("请先配置 MiniMax 或对话接口")
    kind = "idiom" if category != "song" else "song"
    label = "四字成语" if kind == "idiom" else "大家熟悉的中文歌名"
    system = (
        "你给直播看图猜题出题。只输出 JSON 数组。"
        "每项含 emojis, answer, hint。"
        "答案必须真实正确，内容健康，适合抖音全年龄直播，禁止政治、色情、暴力、赌博。"
    )
    prompt = (
        f"请出 {max(1, min(int(count), 12))} 道看图猜{label}。"
        f'category 固定写 "{kind}"。emojis 用 2 到 4 个表情符号表达答案，不要写汉字。'
        "hint 只给很短的提示，不要直接写出答案。不要重复常见烂梗。"
    )
    data = chat_json(prompt, system, temperature=0.6)
    rows = data if isinstance(data, list) else []
    cleaned = []
    for item in rows:
        if isinstance(item, dict):
            item = {**item, "category": kind}
            puzzle = sanitize_puzzle(item)
            if puzzle:
                cleaned.append(puzzle)
    return save_generated(cleaned)
