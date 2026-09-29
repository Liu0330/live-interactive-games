"""记录 MiniMax、对话接口和硅基流动返回的用量。

只保存数字：对话的输入、输出和合计 token，以及接口明确返回的字符数或向量条数。
不保存密钥、请求头和提示词。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

SESSION_STARTED_AT = time.time()
_KINDS = ("chat", "embed", "tts")
_PROVIDERS = ("minimax", "llm", "siliconflow")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _first(sources: list[Any], *keys: str) -> int | None:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            number = _as_int(source.get(key))
            if number is not None:
                return number
    return None


def parse_usage(payload: Any, *, vectors: int | None = None) -> dict[str, int | None]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    extra = payload.get("extra_info") if isinstance(payload, dict) else None
    sources = [usage, extra, payload if isinstance(payload, dict) else None]
    prompt = _first(sources, "prompt_tokens", "input_tokens")
    completion = _first(sources, "completion_tokens", "output_tokens")
    total = _first(sources, "total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    characters = _first(sources, "usage_characters", "characters", "character_count")
    count = _as_int(vectors)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "characters": characters,
        "vectors": count,
    }


def _clean_model(model: str) -> str:
    text = " ".join(str(model or "").split())
    return (text or "unknown")[:80]


def note_call(
    *,
    provider: str,
    kind: str,
    model: str,
    payload: Any = None,
    vectors: int | None = None,
    ok: bool = True,
    created_at: int | None = None,
) -> None:
    if provider not in _PROVIDERS or kind not in _KINDS:
        return
    try:
        usage = parse_usage(payload, vectors=vectors)
        _insert(
            provider=provider,
            kind=kind,
            model=_clean_model(model),
            created_at=int(created_at if created_at is not None else time.time()),
            ok=1 if ok else 0,
            **usage,
        )
    except Exception:
        return


def _insert(**row: Any) -> None:
    from app import db

    with db._lock:
        conn = db._connect()
        try:
            conn.execute(
                """
                INSERT INTO api_usage (
                    created_at, provider, kind, model,
                    prompt_tokens, completion_tokens, total_tokens,
                    characters, vectors, ok
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["created_at"],
                    row["provider"],
                    row["kind"],
                    row["model"],
                    row["prompt_tokens"],
                    row["completion_tokens"],
                    row["total_tokens"],
                    row["characters"],
                    row["vectors"],
                    row["ok"],
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _blank() -> dict[str, int]:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "characters": 0,
        "vectors": 0,
    }


def _window(conn: Any, start: int | None) -> dict[str, dict[str, int]]:
    clause = ""
    args: tuple[Any, ...] = ()
    if start is not None:
        clause = "WHERE created_at >= ?"
        args = (int(start),)
    rows = conn.execute(
        f"""
        SELECT kind,
               COUNT(*) AS calls,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COALESCE(SUM(characters), 0) AS characters,
               COALESCE(SUM(vectors), 0) AS vectors
        FROM api_usage
        {clause}
        GROUP BY kind
        """,
        args,
    ).fetchall()
    out = {kind: _blank() for kind in _KINDS}
    for row in rows:
        kind = str(row["kind"])
        if kind not in out:
            continue
        out[kind] = {
            "calls": int(row["calls"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "characters": int(row["characters"] or 0),
            "vectors": int(row["vectors"] or 0),
        }
    return out


def _optional(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def usage_summary(now: datetime | None = None, session_started: float | None = None) -> dict[str, Any]:
    from app import db

    today = db.period_start("day", now)
    session = int(SESSION_STARTED_AT if session_started is None else session_started)
    with db._lock:
        conn = db._connect()
        try:
            recent_rows = conn.execute(
                """
                SELECT created_at, provider, kind, model,
                       prompt_tokens, completion_tokens, total_tokens,
                       characters, vectors, ok
                FROM api_usage
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()
            summary = {
                "today": _window(conn, today),
                "session": _window(conn, session),
                "all_time": _window(conn, None),
            }
        finally:
            conn.close()
    summary["recent"] = [
        {
            "created_at": int(row["created_at"]),
            "provider": str(row["provider"]),
            "kind": str(row["kind"]),
            "model": str(row["model"]),
            "prompt_tokens": _optional(row["prompt_tokens"]),
            "completion_tokens": _optional(row["completion_tokens"]),
            "total_tokens": _optional(row["total_tokens"]),
            "characters": _optional(row["characters"]),
            "vectors": _optional(row["vectors"]),
            "ok": bool(row["ok"]),
        }
        for row in recent_rows
    ]
    return summary


def reset_usage() -> None:
    from app import db

    with db._lock:
        conn = db._connect()
        try:
            conn.execute("DELETE FROM api_usage")
            conn.commit()
        finally:
            conn.close()
