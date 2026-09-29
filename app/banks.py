"""控制台可改的题库。新增内容写入 SQLite，当前这一局不动，下一局就能抽到。"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import threading
import time

KINDS = ("word", "question", "idiom", "puzzle")
GAME_KIND = {
    "semantic": "word",
    "quiz": "question",
    "idiom": "idiom",
    "emoji": "puzzle",
}

BANNED_TERMS = (
    "习近平",
    "毛泽东",
    "共产党",
    "法轮功",
    "法轮",
    "六四",
    "天安门",
    "台独",
    "藏独",
    "疆独",
    "色情",
    "裸体",
    "裸聊",
    "赌博",
    "赌场",
    "毒品",
    "吸毒",
    "海洛因",
    "冰毒",
    "自杀",
    "恐怖袭击",
    "枪支",
    "嫖娼",
    "淫秽",
    "做爱",
    "性交",
)

def _norm(text: str) -> str:
    value = (text or "").strip().lower()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[，。！？、；：,.!?;:\"'`~]+", "", value)
    return value


_SUGGEST = re.compile(r"^\s*出题[\s:：]+(\S(?:.*\S)?)\s*$")
_HASH = re.compile(r"^#(\S+)\s+(.+)$")
_gen_lock = threading.Lock()
_gen = {kind: 0 for kind in KINDS}
_label_cache: dict[str, object] = {}


def banned_hit(text: str) -> str:
    blob = text or ""
    for term in BANNED_TERMS:
        if term and term in blob:
            return term
    return ""


def generation(kind: str) -> int:
    with _gen_lock:
        return int(_gen.get(kind, 0))


def _touch(kind: str) -> None:
    with _gen_lock:
        _gen[kind] = int(_gen.get(kind, 0)) + 1
    _label_cache.pop(kind, None)


def _conn() -> sqlite3.Connection:
    from app.db import DB_PATH, init_db

    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def copy_legacy_rows(conn: sqlite3.Connection) -> None:
    """把已经写在旧表里的成语和看图猜题目抄进统一题库。不删除旧表。"""
    now = int(time.time())
    if _table_exists(conn, "extra_idioms"):
        for row in conn.execute("SELECT idiom, created_at FROM extra_idioms"):
            word = str(row["idiom"] or "").strip()
            if not word:
                continue
            created = int(row["created_at"] or now)
            _insert_row(
                conn,
                "idiom",
                word,
                "成语",
                word,
                {"idiom": word, "category": "成语"},
                "legacy",
                created,
            )
    if _table_exists(conn, "emoji_puzzles"):
        for row in conn.execute(
            "SELECT category, emojis, answer, aliases, hint, created_at FROM emoji_puzzles"
        ):
            category = str(row["category"] or "idiom")
            answer = str(row["answer"] or "").strip()
            if not answer:
                continue
            try:
                aliases = json.loads(row["aliases"] or "[]")
            except json.JSONDecodeError:
                aliases = []
            if not isinstance(aliases, list):
                aliases = []
            payload = {
                "category": category,
                "emojis": str(row["emojis"] or ""),
                "answer": answer,
                "aliases": aliases,
                "hint": str(row["hint"] or ""),
            }
            created = int(row["created_at"] or now)
            _insert_row(
                conn,
                "puzzle",
                _puzzle_key(category, answer),
                category,
                f"{payload['emojis']} {answer}".strip(),
                payload,
                "legacy",
                created,
            )


def _insert_row(
    conn: sqlite3.Connection,
    kind: str,
    key: str,
    category: str,
    label: str,
    payload: dict,
    source: str,
    created: int | None = None,
    reactivate: bool = False,
) -> int | None:
    now = int(created if created is not None else time.time())
    conflict = (
        """
        ON CONFLICT(kind, dedupe_key) DO UPDATE SET
            category = excluded.category,
            label = excluded.label,
            payload = excluded.payload,
            source = excluded.source,
            enabled = 1,
            updated_at = excluded.updated_at
        """
        if reactivate
        else "ON CONFLICT(kind, dedupe_key) DO NOTHING"
    )
    before = conn.total_changes
    conn.execute(
        f"""
        INSERT INTO bank_items (
            kind, dedupe_key, category, label, payload, source, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        {conflict}
        """,
        (kind, key, category or "", label, json.dumps(payload, ensure_ascii=False), source or "manual", now, now),
    )
    if conn.total_changes == before:
        return None
    if reactivate:
        conn.execute("DELETE FROM bank_hidden WHERE kind = ? AND dedupe_key = ?", (kind, key))
    row = conn.execute(
        "SELECT id FROM bank_items WHERE kind = ? AND dedupe_key = ?",
        (kind, key),
    ).fetchone()
    return int(row["id"]) if row else None


def _puzzle_key(category: str, answer: str) -> str:
    return f"{_puzzle_category(category)}:{_norm(answer)}"


def _puzzle_category(category: str) -> str:
    text = (category or "").strip()
    if text in {"成语", "idioms", "idiom"}:
        return "idiom"
    if text in {"歌", "歌名", "songs", "song"}:
        return "song"
    return text or "idiom"


def _word_key(word: str) -> str:
    return _norm(word)


def _question_key(question: str) -> str:
    return _norm(question)


def hidden_keys(kind: str) -> set[str]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT dedupe_key FROM bank_hidden WHERE kind = ?", (kind,)).fetchall()
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()
    return {str(row["dedupe_key"]) for row in rows}


def _hide(conn: sqlite3.Connection, kind: str, key: str) -> None:
    if not key:
        return
    conn.execute(
        "INSERT INTO bank_hidden (kind, dedupe_key) VALUES (?, ?) ON CONFLICT(kind, dedupe_key) DO NOTHING",
        (kind, key),
    )


def _rows(kind: str) -> list[sqlite3.Row]:
    conn = _conn()
    try:
        return conn.execute(
            """
            SELECT id, kind, dedupe_key, category, label, payload, source, enabled
            FROM bank_items WHERE kind = ? AND enabled = 1
            ORDER BY id DESC
            """,
            (kind,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _payload(row: sqlite3.Row) -> dict:
    try:
        data = json.loads(row["payload"] or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def playable_words() -> list[str]:
    from app.games.wordbank import load_words

    hidden = hidden_keys("word")
    seen: set[str] = set()
    out: list[str] = []
    for word in list(load_words()) + _enabled_labels("word"):
        key = _word_key(word)
        if not key or key in hidden or key in seen:
            continue
        seen.add(key)
        out.append(word.strip())
    return out


def _enabled_labels(kind: str) -> list[str]:
    from app.db import DB_PATH

    token = (str(DB_PATH), generation(kind))
    cached = _label_cache.get(kind)
    if isinstance(cached, tuple) and cached[0] == token:
        return list(cached[1])
    labels = []
    for row in _rows(kind):
        label = str(row["label"] or "").strip()
        if label:
            labels.append(label)
    _label_cache[kind] = (token, list(labels))
    return labels


def idiom_labels() -> set[str]:
    from app.games.idiom_bank import clean_idiom

    found = set()
    for label in _enabled_labels("idiom"):
        word = clean_idiom(label)
        if word:
            found.add(word)
    return found


def category_of(kind: str, label: str) -> str:
    key = _word_key(label) if kind != "puzzle" else label
    for row in _rows(kind):
        if kind == "word" and _word_key(str(row["label"])) == _word_key(label):
            return str(row["category"] or "")
        if kind == "idiom" and str(row["dedupe_key"]) == label:
            return str(row["category"] or "")
        if str(row["dedupe_key"]) == key:
            return str(row["category"] or "")
    return ""


def question_items() -> list[dict]:
    hidden = hidden_keys("question")
    out = []
    for row in _rows("question"):
        if str(row["dedupe_key"]) in hidden:
            continue
        payload = _payload(row)
        question = str(payload.get("question") or row["label"] or "").strip()
        if not question:
            continue
        out.append(
            {
                "question": question,
                "options": payload.get("options") or [],
                "answer": payload.get("answer") or "",
                "aliases": payload.get("aliases") or [],
                "category": str(row["category"] or payload.get("category") or ""),
                "id": f"bank:{row['id']}",
            }
        )
    return out


def merged_puzzles() -> list[dict]:
    from app.games.emoji_bank import bundled_puzzles, hidden_keys as bundle_hidden, stored_puzzles

    hidden_ids = bundle_hidden()
    hidden_answers = hidden_keys("puzzle")
    order: list[dict] = []
    index: dict[str, int] = {}

    def put(item: dict) -> None:
        answer = str(item.get("answer") or "")
        category = _puzzle_category(str(item.get("category") or ""))
        key = _puzzle_key(category, answer)
        if not answer or key in hidden_answers or str(item.get("id") or "") in hidden_ids:
            return
        item = {**item, "category": category}
        if key in index:
            order[index[key]] = item
        else:
            index[key] = len(order)
            order.append(item)

    for item in bundled_puzzles():
        put(item)
    for item in stored_puzzles():
        put(item)
    for row in _rows("puzzle"):
        payload = _payload(row)
        put(
            {
                "id": f"bank:{row['id']}",
                "category": payload.get("category") or row["category"],
                "emojis": payload.get("emojis") or "",
                "answer": payload.get("answer") or "",
                "aliases": payload.get("aliases") or [],
                "hint": payload.get("hint") or "",
                "source": row["source"] or "manual",
            }
        )
    return order


def _clean_word(word: str, category: str) -> dict | None:
    from app.games.wordbank import sanitize_generated

    cleaned = sanitize_generated([word])
    if not cleaned:
        return None
    text = cleaned[0]
    if banned_hit(text + category):
        return None
    return {"word": text, "category": (category or "未分类").strip() or "未分类"}


def _clean_idiom(word: str, category: str) -> dict | None:
    from app.games.idiom_bank import clean_idiom

    text = clean_idiom(word)
    if not text or banned_hit(text + category):
        return None
    return {"idiom": text, "category": (category or "成语").strip() or "成语"}


def _clean_question(question: str, options: list, answer: str, category: str, aliases: list | None = None) -> dict | None:
    prompt = (question or "").strip()
    choices = [str(item).strip() for item in options if str(item).strip()]
    target = (answer or "").strip()
    cat = (category or "常识").strip() or "常识"
    alias_list = [str(item).strip() for item in (aliases or []) if str(item).strip()]
    blob = prompt + cat + target + "".join(choices) + "".join(alias_list)
    if not prompt or len(choices) < 2 or not target or banned_hit(blob):
        return None
    if len(prompt) > 80 or any(len(item) > 40 for item in choices):
        return None
    known = {_norm(item) for item in choices}
    if _norm(target) not in known and target not in {"A", "B", "C", "D"}:
        return None
    return {
        "question": prompt,
        "options": choices[:4],
        "answer": target,
        "aliases": alias_list[:4],
        "category": cat,
    }


def _clean_puzzle(category: str, emojis: str, answer: str, hint: str = "", aliases: list | None = None) -> dict | None:
    from app.games.emoji_bank import sanitize_puzzle

    item = sanitize_puzzle(
        {
            "category": _puzzle_category(category),
            "emojis": emojis,
            "answer": answer,
            "hint": hint,
            "aliases": aliases or [],
        }
    )
    if not item:
        return None
    item["category"] = _puzzle_category(item["category"])
    return item


def _split_fields(line: str) -> list[str]:
    if "|" in line:
        return [part.strip() for part in line.split("|")]
    if "," in line:
        return [part.strip() for part in next(csv.reader([line]))]
    return [line.strip()]


def _parse_line(kind: str, line: str, category: str) -> tuple[dict | None, str]:
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None, ""
    if raw.split("|")[0].split(",")[0].strip() in {"分类", "category"} and any(
        token in raw for token in ("内容", "题干", "表情", "词语")
    ):
        return None, ""
    if banned_hit(raw + category):
        return None, f"含有不适合直播的内容：{raw}"
    parts = _split_fields(raw)
    if kind == "word":
        if len(parts) >= 2:
            item = _clean_word(parts[-1], parts[0] or category)
        else:
            item = _clean_word(parts[0], category)
        return (item, "") if item else (None, f"词语无效：{raw}")
    if kind == "idiom":
        if len(parts) >= 2 and not _looks_idiom(parts[0]):
            item = _clean_idiom(parts[-1], parts[0] or category)
        else:
            item = _clean_idiom(parts[0], category)
        return (item, "") if item else (None, f"需要四个汉字的成语：{raw}")
    if kind == "question":
        if len(parts) >= 7:
            item = _clean_question(parts[1], parts[2:6], parts[6], parts[0] or category)
        elif len(parts) >= 6:
            item = _clean_question(parts[0], parts[1:5], parts[5], category)
        else:
            item = None
        return (item, "") if item else (None, f"题目格式应为 题干|选项|选项|选项|选项|答案：{raw}")
    if len(parts) >= 4 and parts[0] in {"成语", "歌名", "idiom", "song"}:
        item = _clean_puzzle(parts[0], parts[1], parts[2], parts[3])
    elif len(parts) >= 3 and parts[0] in {"成语", "歌名", "idiom", "song"}:
        item = _clean_puzzle(parts[0], parts[1], parts[2], "")
    elif len(parts) >= 3:
        item = _clean_puzzle(category or "idiom", parts[0], parts[1], parts[2])
    elif len(parts) >= 2:
        item = _clean_puzzle(category or "idiom", parts[0], parts[1], "")
    else:
        item = None
    return (item, "") if item else (None, f"看图猜格式应为 表情|答案：{raw}")


def _looks_idiom(text: str) -> bool:
    from app.games.idiom_bank import clean_idiom

    return bool(clean_idiom(text))


def _existing_keys(kind: str) -> set[str]:
    return {str(item["key"]) for item in _catalog_records(kind) if item.get("key")}


def _item_key(kind: str, item: dict) -> str:
    if kind == "word":
        return _word_key(item.get("word") or "")
    if kind == "idiom":
        return str(item.get("idiom") or "")
    if kind == "question":
        return _question_key(item.get("question") or "")
    return _puzzle_key(item.get("category") or "", item.get("answer") or "")


def _label_of(kind: str, item: dict) -> str:
    if kind == "word":
        return str(item.get("word") or "")
    if kind == "idiom":
        return str(item.get("idiom") or "")
    if kind == "question":
        return str(item.get("question") or "")
    return f"{item.get('emojis') or ''} {item.get('answer') or ''}".strip()


def add_items(kind: str, items: list[dict], source: str = "manual") -> dict:
    if kind not in KINDS:
        raise ValueError("未知题库")
    added = []
    skipped = []
    rejected = []
    seen = _existing_keys(kind)
    conn = _conn()
    try:
        for item in items:
            key = _item_key(kind, item)
            label = _label_of(kind, item)
            if not key:
                rejected.append(label or "空内容")
                continue
            if key in seen:
                skipped.append(label)
                continue
            category = str(item.get("category") or "")
            row_id = _insert_row(conn, kind, key, category, label, item, source, reactivate=True)
            if not row_id:
                skipped.append(label)
                continue
            if kind == "idiom":
                conn.execute(
                    "INSERT INTO extra_idioms (idiom, created_at) VALUES (?, ?) ON CONFLICT(idiom) DO NOTHING",
                    (key, int(time.time())),
                )
            seen.add(key)
            added.append({**item, "id": f"bank:{row_id}", "kind": kind})
        conn.commit()
    finally:
        conn.close()
    if added:
        _touch(kind)
        if kind == "idiom":
            from app.games.idiom_bank import reset_idiom_cache

            reset_idiom_cache()
    return {"added": added, "skipped": skipped, "rejected": rejected}


def add_text(kind: str, text: str, category: str = "", source: str = "manual") -> dict:
    items = []
    rejected = []
    for line in (text or "").splitlines()[:500]:
        item, reason = _parse_line(kind, line, category)
        if reason:
            rejected.append(reason)
        elif item:
            items.append(item)
    if not items and not rejected and (text or "").strip():
        item, reason = _parse_line(kind, text, category)
        if reason:
            rejected.append(reason)
        elif item:
            items.append(item)
    result = add_items(kind, items, source=source)
    result["rejected"] = rejected + result["rejected"]
    return result


def draft(kind: str, category: str, theme: str, count: int) -> list[dict]:
    from app.config import chat_ready
    from app.llm import ChatError, chat_json

    if kind not in KINDS:
        raise ValueError("未知题库")
    if not chat_ready():
        raise ChatError("请先配置 MiniMax 或对话接口")
    limit = {"word": 80, "question": 20, "idiom": 40, "puzzle": 12}[kind]
    n = max(1, min(int(count or 1), limit))
    theme = (theme or category or "日常生活").strip()
    category = (category or "").strip()
    system = (
        "你是直播互动游戏的出题助手，使用简洁中文。只输出 JSON 数组。"
        "内容必须健康、适合抖音全年龄直播，禁止政治、色情、暴力、赌博、毒品。"
    )
    if kind == "word":
        prompt = (
            f"生成 {n} 个适合语义猜词的中文词语，主题：{theme}，分类：{category or '未分类'}。"
            '每项是 {{"word":"词语","category":"分类"}}，2 到 4 个字，不要重复。'
        )
    elif kind == "question":
        prompt = (
            f"生成 {n} 道{theme}选择题，分类：{category or '常识'}。"
            '每项含 question, options(正好4个), answer, category。answer 必须是 options 里的原文。'
        )
    elif kind == "idiom":
        prompt = (
            f"生成 {n} 个真实常见的四字成语，主题：{theme}。"
            '每项是 {{"idiom":"成语","category":"成语"}}，必须是四个汉字。'
        )
    else:
        kind_name = "歌名" if _puzzle_category(category) == "song" else "四字成语"
        fixed = "song" if kind_name == "歌名" else "idiom"
        prompt = (
            f"出 {n} 道看图猜{kind_name}。每项含 category, emojis, answer, hint。"
            f'category 固定为 "{fixed}"。emojis 用 2 到 4 个表情，不要写汉字。hint 不要直接写出答案。'
        )
    data = chat_json(prompt, system, temperature=0.6)
    rows = data if isinstance(data, list) else []
    cleaned = []
    seen = set()
    existing = _existing_keys(kind)
    for raw in rows:
        item = _draft_item(kind, raw, category)
        if not item:
            continue
        key = _item_key(kind, item)
        if not key or key in seen:
            continue
        seen.add(key)
        item["duplicate"] = key in existing
        cleaned.append(item)
    return cleaned


def _draft_item(kind: str, raw, category: str) -> dict | None:
    if kind == "word":
        if isinstance(raw, str):
            return _clean_word(raw, category)
        if isinstance(raw, dict):
            return _clean_word(str(raw.get("word") or raw.get("词语") or ""), str(raw.get("category") or category))
        return None
    if kind == "idiom":
        if isinstance(raw, str):
            return _clean_idiom(raw, category or "成语")
        if isinstance(raw, dict):
            return _clean_idiom(str(raw.get("idiom") or raw.get("成语") or ""), str(raw.get("category") or category or "成语"))
        return None
    if kind == "question" and isinstance(raw, dict):
        return _clean_question(
            str(raw.get("question") or ""),
            raw.get("options") or [],
            str(raw.get("answer") or ""),
            str(raw.get("category") or category or "常识"),
            raw.get("aliases") or [],
        )
    if kind == "puzzle" and isinstance(raw, dict):
        return _clean_puzzle(
            str(raw.get("category") or category or "idiom"),
            str(raw.get("emojis") or ""),
            str(raw.get("answer") or ""),
            str(raw.get("hint") or ""),
            raw.get("aliases") or [],
        )
    return None


def save_drafts(kind: str, items: list[dict], source: str = "ai") -> dict:
    fresh = [item for item in items if isinstance(item, dict) and not item.get("duplicate")]
    return add_items(kind, fresh, source=source)


def capture_suggestion(content: str, nickname: str, user_id: str, game_id: str) -> dict | None:
    matched = _SUGGEST.match(content or "")
    if not matched:
        return None
    body = matched.group(1).strip()
    category = ""
    hashed = _HASH.match(body)
    if hashed:
        category, body = hashed.group(1), hashed.group(2).strip()
    kind = GAME_KIND.get(game_id or "", "word")
    if not body:
        return {"ok": False, "reason": "空内容"}
    status = "blocked" if banned_hit(body + category) else "pending"
    conn = _conn()
    try:
        existing = conn.execute(
            """
            SELECT id FROM bank_suggestions
            WHERE kind = ? AND text = ? AND status = 'pending'
            """,
            (kind, body),
        ).fetchone()
        if existing and status == "pending":
            return {"ok": True, "id": int(existing["id"]), "status": "pending", "duplicate": True}
        cur = conn.execute(
            """
            INSERT INTO bank_suggestions (kind, category, text, nickname, user_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (kind, category, body[:200], nickname or "观众", user_id or "", status, int(time.time())),
        )
        conn.commit()
        return {"ok": status == "pending", "id": int(cur.lastrowid), "status": status, "kind": kind}
    finally:
        conn.close()


def list_suggestions(kind: str = "") -> list[dict]:
    conn = _conn()
    try:
        if kind in KINDS:
            rows = conn.execute(
                """
                SELECT id, kind, category, text, nickname, user_id, status, created_at
                FROM bank_suggestions WHERE status = 'pending' AND kind = ?
                ORDER BY id DESC LIMIT 50
                """,
                (kind,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, kind, category, text, nickname, user_id, status, created_at
                FROM bank_suggestions WHERE status = 'pending'
                ORDER BY id DESC LIMIT 50
                """
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [dict(row) for row in rows]


def review_suggestion(suggestion_id: int, action: str) -> dict:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM bank_suggestions WHERE id = ?", (int(suggestion_id),)).fetchone()
        if not row:
            raise ValueError("找不到这条出题")
        if row["status"] == "blocked" or action == "reject":
            conn.execute("UPDATE bank_suggestions SET status = 'rejected' WHERE id = ?", (int(suggestion_id),))
            conn.commit()
            return {"ok": True, "status": "rejected"}
        if action != "approve":
            raise ValueError("未知操作")
        if row["status"] != "pending":
            raise ValueError("这条已经处理过了")
        kind = str(row["kind"])
        text = str(row["text"])
        category = str(row["category"] or "")
    finally:
        conn.close()
    result = add_text(kind, text, category, source="viewer")
    status = "approved" if result["added"] else "rejected"
    conn = _conn()
    try:
        conn.execute("UPDATE bank_suggestions SET status = ? WHERE id = ?", (status, int(suggestion_id)))
        conn.commit()
    finally:
        conn.close()
    if not result["added"]:
        reason = (result["rejected"] or result["skipped"] or ["无法入库"])[0]
        raise ValueError(str(reason))
    return {"ok": True, "status": status, **result}


def catalog(kind: str, query: str = "", category: str = "", limit: int = 80) -> dict:
    if kind not in KINDS:
        raise ValueError("未知题库")
    records = _catalog_records(kind)
    needle = (query or "").strip().lower()
    cat = (category or "").strip().lower()
    filtered = []
    for item in records:
        blob = f"{item['label']} {item['category']} {item.get('line') or ''}".lower()
        if needle and needle not in blob:
            continue
        if cat and cat not in (item["category"] or "").lower():
            continue
        filtered.append(item)
    return {
        "kind": kind,
        "total": len(filtered),
        "items": filtered[: max(1, min(int(limit or 80), 200))],
        "suggestions": list_suggestions(kind),
    }


def _catalog_records(kind: str) -> list[dict]:
    hidden = hidden_keys(kind)
    records = []
    seen = set()
    if kind == "word":
        from app.games.wordbank import classify_word, load_words

        for word in load_words():
            key = _word_key(word)
            if key in hidden:
                continue
            records.append(_public_record(f"file:word:{word}", kind, key, classify_word(word), word, "bundle", {"word": word}))
            seen.add(key)
    elif kind == "question":
        from app.games.quiz import load_question_file

        for item in load_question_file():
            key = _question_key(str(item.get("question") or ""))
            if not key or key in hidden:
                continue
            records.append(
                _public_record(
                    f"file:question:{key}",
                    kind,
                    key,
                    str(item.get("category") or "常识"),
                    str(item.get("question") or ""),
                    "bundle",
                    item,
                )
            )
            seen.add(key)
    elif kind == "idiom":
        from app.games.idiom_bank import bundled_idioms

        for word in bundled_idioms():
            if word in hidden:
                continue
            records.append(_public_record(f"file:idiom:{word}", kind, word, "成语", word, "bundle", {"idiom": word}))
            seen.add(word)
    else:
        for item in merged_puzzles():
            key = _puzzle_key(item.get("category") or "", item.get("answer") or "")
            if key in seen:
                continue
            records.append(
                _public_record(
                    str(item.get("id") or key),
                    kind,
                    key,
                    _puzzle_category(str(item.get("category") or "")),
                    f"{item.get('emojis') or ''} {item.get('answer') or ''}".strip(),
                    str(item.get("source") or "bundle"),
                    item,
                )
            )
            seen.add(key)
        return records
    for row in _rows(kind):
        key = str(row["dedupe_key"])
        if key in hidden:
            continue
        payload = _payload(row)
        record = _public_record(
            f"bank:{row['id']}",
            kind,
            key,
            str(row["category"] or ""),
            str(row["label"] or ""),
            str(row["source"] or "manual"),
            payload,
        )
        if key in seen:
            for index, old in enumerate(records):
                if old["key"] == key:
                    records[index] = record
                    break
        else:
            records.append(record)
            seen.add(key)
    return records


def _public_record(item_id: str, kind: str, key: str, category: str, label: str, source: str, payload: dict) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "key": key,
        "category": category,
        "label": label,
        "source": source,
        "line": _export_line(kind, category, payload if payload else {"word": label}),
        "payload": payload,
    }


def _export_line(kind: str, category: str, payload: dict) -> str:
    if kind == "word":
        return f"{category}|{payload.get('word') or payload.get('label') or ''}"
    if kind == "idiom":
        return f"{category}|{payload.get('idiom') or ''}"
    if kind == "question":
        options = list(payload.get("options") or [])
        while len(options) < 4:
            options.append("")
        return "|".join([category, str(payload.get("question") or ""), *options[:4], str(payload.get("answer") or "")])
    cat = "歌名" if _puzzle_category(str(payload.get("category") or category)) == "song" else "成语"
    return "|".join([cat, str(payload.get("emojis") or ""), str(payload.get("answer") or ""), str(payload.get("hint") or "")])


def export_text(kind: str, fmt: str) -> tuple[str, str, str]:
    records = _catalog_records(kind)
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        if kind == "question":
            writer.writerow(["分类", "题干", "选项A", "选项B", "选项C", "选项D", "答案"])
        elif kind == "puzzle":
            writer.writerow(["分类", "表情", "答案", "提示"])
        else:
            writer.writerow(["分类", "内容"])
        for item in records:
            writer.writerow(_split_fields(item["line"]))
        return buffer.getvalue(), f"{kind}.csv", "text/csv; charset=utf-8"
    lines = [item["line"] for item in records]
    return "\n".join(lines) + ("\n" if lines else ""), f"{kind}.txt", "text/plain; charset=utf-8"


def update_item(item_id: str, category: str, label: str, extra: str = "", text: str = "") -> dict:
    record = _find_record(item_id)
    if not record:
        raise ValueError("找不到这条内容")
    kind = record["kind"]
    if not (text or "").strip():
        text = _edit_text(kind, category or record["category"], label, extra, record["payload"])
    parsed, reason = _parse_line(kind, text, category or record["category"])
    if not parsed:
        raise ValueError(reason or "内容无效")
    remove_item(item_id)
    result = add_items(kind, [parsed], source="manual")
    if not result["added"]:
        raise ValueError((result["rejected"] or result["skipped"] or ["没有写入"])[0])
    return result["added"][0]


def _edit_text(kind: str, category: str, label: str, extra: str, payload: dict) -> str:
    label = (label or "").strip()
    extra = (extra or "").strip()
    if kind == "word":
        return f"{category}|{label}"
    if kind == "idiom":
        return f"{category}|{label}"
    if kind == "puzzle":
        emojis, hint = (payload.get("emojis") or ""), (payload.get("hint") or "")
        if "|" in extra:
            parts = _split_fields(extra)
            emojis = parts[0] or emojis
            hint = parts[1] if len(parts) > 1 else hint
        elif extra:
            emojis = extra
        return f"{_puzzle_category(category)}|{emojis}|{label or payload.get('answer') or ''}|{hint}"
    options = list(payload.get("options") or [])
    answer = str(payload.get("answer") or "")
    if extra:
        parts = _split_fields(extra)
        if len(parts) >= 5:
            options, answer = parts[:4], parts[4]
        elif len(parts) >= 4:
            options = parts[:4]
    while len(options) < 4:
        options.append("")
    return "|".join([category or "常识", label or str(payload.get("question") or ""), *options[:4], answer])


def _find_record(item_id: str) -> dict | None:
    for kind in KINDS:
        for item in _catalog_records(kind):
            if item["id"] == item_id:
                return item
    return None


def remove_item(item_id: str) -> None:
    key = (item_id or "").strip()
    if not key:
        return
    if key.startswith("bank:"):
        _disable_bank(key)
        return
    if key.startswith("file:"):
        _hide_file(key)
        return
    if key.startswith("db:"):
        _hide_emoji_row(key)
        return
    _hide_bundled_puzzle(key)


def _disable_bank(item_id: str) -> None:
    try:
        row_id = int(item_id.split(":", 1)[1])
    except ValueError:
        return
    conn = _conn()
    try:
        row = conn.execute("SELECT kind, dedupe_key FROM bank_items WHERE id = ?", (row_id,)).fetchone()
        if not row:
            return
        conn.execute("UPDATE bank_items SET enabled = 0, updated_at = ? WHERE id = ?", (int(time.time()), row_id))
        _hide(conn, str(row["kind"]), str(row["dedupe_key"]))
        if row["kind"] == "idiom":
            conn.execute("DELETE FROM extra_idioms WHERE idiom = ?", (row["dedupe_key"],))
        if row["kind"] == "puzzle":
            _hide_puzzle_answer(conn, str(row["dedupe_key"]))
        conn.commit()
        kind = str(row["kind"])
    finally:
        conn.close()
    _touch(kind)
    if kind == "idiom":
        from app.games.idiom_bank import reset_idiom_cache

        reset_idiom_cache()


def _hide_file(item_id: str) -> None:
    parts = item_id.split(":", 2)
    if len(parts) < 3:
        return
    kind, key = parts[1], parts[2]
    if kind not in KINDS:
        return
    conn = _conn()
    try:
        _hide(conn, kind, key)
        conn.execute(
            "UPDATE bank_items SET enabled = 0, updated_at = ? WHERE kind = ? AND dedupe_key = ?",
            (int(time.time()), kind, key),
        )
        if kind == "idiom":
            conn.execute("DELETE FROM extra_idioms WHERE idiom = ?", (key,))
        conn.commit()
    finally:
        conn.close()
    _touch(kind)
    if kind == "idiom":
        from app.games.idiom_bank import reset_idiom_cache

        reset_idiom_cache()


def _hide_emoji_row(item_id: str) -> None:
    try:
        row_id = int(item_id.split(":", 1)[1])
    except ValueError:
        return
    conn = _conn()
    try:
        row = conn.execute("SELECT category, answer FROM emoji_puzzles WHERE id = ?", (row_id,)).fetchone()
        conn.execute("DELETE FROM emoji_puzzles WHERE id = ?", (row_id,))
        if row:
            _hide_puzzle_answer(conn, _puzzle_key(str(row["category"]), str(row["answer"])))
        conn.commit()
    finally:
        conn.close()
    _touch("puzzle")


def _hide_bundled_puzzle(puzzle_id: str) -> None:
    from app.games.emoji_bank import bundled_puzzles

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO emoji_hidden (puzzle_key) VALUES (?) ON CONFLICT(puzzle_key) DO NOTHING",
            (puzzle_id,),
        )
        for item in bundled_puzzles():
            if str(item.get("id")) == puzzle_id:
                _hide_puzzle_answer(conn, _puzzle_key(str(item.get("category") or ""), str(item.get("answer") or "")))
        conn.commit()
    finally:
        conn.close()
    _touch("puzzle")


def _hide_puzzle_answer(conn: sqlite3.Connection, key: str) -> None:
    _hide(conn, "puzzle", key)
    if ":" not in key:
        return
    category, answer = key.split(":", 1)
    conn.execute(
        "UPDATE bank_items SET enabled = 0, updated_at = ? WHERE kind = 'puzzle' AND dedupe_key = ?",
        (int(time.time()), key),
    )
    conn.execute("DELETE FROM emoji_puzzles WHERE category = ? AND answer = ?", (category, answer))


def upsert_idiom(word: str, source: str = "model") -> None:
    item = _clean_idiom(word, "成语")
    if not item:
        return
    add_items("idiom", [item], source=source)
