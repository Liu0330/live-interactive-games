import json
import sqlite3
from datetime import datetime

import httpx

import app.games  # noqa: F401
from app.db import add_points, init_db
from app.llm import chat_completion
from app.minimax import complete_chat, embed_texts, synthesize_speech
from app.siliconflow import chat_json, embed_texts as silicon_embed, synthesize_speech as silicon_tts
from app.usage import note_call, parse_usage, reset_usage, usage_summary


def _db(monkeypatch, tmp_path):
    path = tmp_path / "usage.db"
    monkeypatch.setattr("app.db.DB_PATH", path)
    monkeypatch.setattr("app.migrate.DB_PATH", path)
    init_db()
    return path


def test_parse_chat_usage_from_openai_and_anthropic_shapes():
    openai = parse_usage(
        {"usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}, "api_key": "sk-secret"}
    )
    assert openai == {
        "prompt_tokens": 11,
        "completion_tokens": 4,
        "total_tokens": 15,
        "characters": None,
        "vectors": None,
    }
    anthropic = parse_usage({"usage": {"input_tokens": 8, "output_tokens": 3}})
    assert anthropic["prompt_tokens"] == 8
    assert anthropic["completion_tokens"] == 3
    assert anthropic["total_tokens"] == 11
    assert "sk-secret" not in json.dumps(openai)


def test_parse_tts_characters_and_embed_vectors_only_when_returned():
    speech = parse_usage({"extra_info": {"usage_characters": 18, "audio_length": 900}})
    assert speech["characters"] == 18
    assert speech["prompt_tokens"] is None
    embed = parse_usage(
        {"vectors": [[0.1, 0.2], [0.3, 0.4]], "usage": {"total_tokens": 6}},
        vectors=2,
    )
    assert embed["vectors"] == 2
    assert embed["total_tokens"] == 6
    silent = parse_usage(None)
    assert silent["characters"] is None
    assert silent["vectors"] is None
    assert silent["total_tokens"] is None


def test_summary_splits_today_session_and_all_time_by_kind(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    now = datetime(2026, 9, 29, 15, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
    noon = int(now.timestamp())
    yesterday = noon - 86400
    note_call(
        provider="minimax",
        kind="chat",
        model="MiniMax-M3",
        payload={"usage": {"input_tokens": 10, "output_tokens": 5}},
        created_at=yesterday,
    )
    note_call(
        provider="llm",
        kind="chat",
        model="glm-5.2",
        payload={"usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
        created_at=noon,
    )
    note_call(
        provider="minimax",
        kind="embed",
        model="embo-01",
        payload={"usage": {"total_tokens": 7}},
        vectors=2,
        created_at=noon,
    )
    note_call(
        provider="minimax",
        kind="tts",
        model="speech-02-turbo",
        payload={"extra_info": {"usage_characters": 12}},
        created_at=noon,
    )
    note_call(
        provider="siliconflow",
        kind="tts",
        model="FunAudioLLM/CosyVoice2-0.5B",
        payload=b"mp3",
        created_at=noon,
        ok=True,
    )
    summary = usage_summary(now=now, session_started=noon - 10)
    assert summary["today"]["chat"]["calls"] == 1
    assert summary["today"]["chat"]["prompt_tokens"] == 3
    assert summary["today"]["chat"]["completion_tokens"] == 1
    assert summary["today"]["chat"]["total_tokens"] == 4
    assert summary["today"]["embed"]["vectors"] == 2
    assert summary["today"]["embed"]["total_tokens"] == 7
    assert summary["today"]["tts"]["calls"] == 2
    assert summary["today"]["tts"]["characters"] == 12
    assert summary["session"]["chat"]["calls"] == 1
    assert summary["all_time"]["chat"]["calls"] == 2
    assert summary["all_time"]["chat"]["prompt_tokens"] == 13
    assert summary["all_time"]["chat"]["total_tokens"] == 19
    recent = summary["recent"]
    assert len(recent) == 5
    assert recent[0]["kind"] == "tts"
    assert recent[0]["provider"] == "siliconflow"
    assert recent[0]["characters"] is None
    blob = json.dumps(summary)
    assert "sk-" not in blob
    assert "api_key" not in blob


def test_recent_keeps_twenty_and_reset_clears_usage_only(monkeypatch, tmp_path):
    path = _db(monkeypatch, tmp_path)
    add_points("u1", "甲", 9)
    for index in range(25):
        note_call(
            provider="llm",
            kind="chat",
            model="m",
            payload={"usage": {"prompt_tokens": index, "completion_tokens": 1, "total_tokens": index + 1}},
        )
    summary = usage_summary()
    assert summary["all_time"]["chat"]["calls"] == 25
    assert len(summary["recent"]) == 20
    assert summary["recent"][0]["prompt_tokens"] == 24
    assert summary["recent"][-1]["prompt_tokens"] == 5
    reset_usage()
    cleared = usage_summary()
    assert cleared["all_time"]["chat"]["calls"] == 0
    assert cleared["recent"] == []
    conn = sqlite3.connect(path)
    try:
        points = conn.execute("SELECT points FROM scores WHERE user_id = 'u1'").fetchone()[0]
    finally:
        conn.close()
    assert points == 9


def test_minimax_llm_and_siliconflow_calls_record_returned_usage(monkeypatch, tmp_path):
    path = _db(monkeypatch, tmp_path)
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax-secret")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com")
    monkeypatch.setenv("MINIMAX_CHAT_MODEL", "MiniMax-M3")
    monkeypatch.setenv("MINIMAX_EMBED_MODEL", "embo-01")
    monkeypatch.setenv("MINIMAX_TTS_MODEL", "speech-02-turbo")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.setattr("app.config._cache", None)

    def minimax_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "pong"}],
                    "usage": {"input_tokens": 9, "output_tokens": 2},
                    "api_key": request.headers.get("x-api-key"),
                },
            )
        if url.endswith("/embeddings"):
            body = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={"vectors": [[0.2, 0.4] for _ in body["texts"]], "usage": {"total_tokens": 5}},
            )
        return httpx.Response(
            200,
            json={
                "data": {"audio": "494433"},
                "extra_info": {"usage_characters": 6},
                "base_resp": {"status_code": 0},
            },
        )

    transport = httpx.MockTransport(minimax_handler)
    real = httpx.Client

    def minimax_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr("app.minimax.httpx.Client", minimax_client)
    assert complete_chat([{"role": "user", "content": "ping"}]) == "pong"
    assert embed_texts(["月亮"], "db") == [[0.2, 0.4]]
    assert synthesize_speech("谢谢") == bytes.fromhex("494433")

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-llm-secret")
    monkeypatch.setenv("LLM_MODEL", "glm-5.2")
    monkeypatch.setattr("app.config._cache", None)

    def llm_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "pong"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            },
        )

    llm_transport = httpx.MockTransport(llm_handler)

    def llm_client(*args, **kwargs):
        kwargs["transport"] = llm_transport
        return real(*args, **kwargs)

    monkeypatch.setattr("app.llm.httpx.Client", llm_client)
    assert chat_completion([{"role": "user", "content": "hi"}]) == "pong"

    monkeypatch.setattr("app.siliconflow.api_key", lambda: "sk-sf-secret")
    monkeypatch.setattr(
        "app.siliconflow.load_config",
        lambda: {"chat_model": "deepseek-ai/DeepSeek-V3", "embed_model": "BAAI/bge-m3", "tts_model": "FunAudioLLM/CosyVoice2-0.5B", "tts_voice": "bella"},
    )

    def silicon_handler(request: httpx.Request) -> httpx.Response:
        assert "sk-sf-secret" in request.headers.get("authorization", "")
        if str(request.url).endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "[\"春卷\"]"}}],
                    "usage": {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8},
                },
            )
        if str(request.url).endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                    "usage": {"prompt_tokens": 3, "total_tokens": 3},
                },
            )
        return httpx.Response(200, content=b"mp3-bytes")

    sf_transport = httpx.MockTransport(silicon_handler)

    def sf_client(*args, **kwargs):
        kwargs["transport"] = sf_transport
        return real(*args, **kwargs)

    monkeypatch.setattr("app.siliconflow.httpx.Client", sf_client)
    assert chat_json("生成", "只输出 JSON") == ["春卷"]
    assert silicon_embed(["春卷"]) == [[0.1, 0.2]]
    assert silicon_tts("你好") == b"mp3-bytes"

    summary = usage_summary()
    kinds = {}
    for row in summary["recent"]:
        kinds.setdefault((row["provider"], row["kind"]), []).append(row)
        assert "sk-" not in json.dumps(row)
    assert kinds[("minimax", "chat")][0]["prompt_tokens"] == 9
    assert kinds[("minimax", "chat")][0]["completion_tokens"] == 2
    assert kinds[("minimax", "chat")][0]["total_tokens"] == 11
    assert kinds[("minimax", "embed")][0]["vectors"] == 1
    assert kinds[("minimax", "embed")][0]["total_tokens"] == 5
    assert kinds[("minimax", "tts")][0]["characters"] == 6
    assert kinds[("llm", "chat")][0]["total_tokens"] == 5
    assert kinds[("siliconflow", "chat")][0]["total_tokens"] == 8
    assert kinds[("siliconflow", "embed")][0]["vectors"] == 1
    assert kinds[("siliconflow", "embed")][0]["total_tokens"] == 3
    assert kinds[("siliconflow", "tts")][0]["characters"] is None
    conn = sqlite3.connect(path)
    try:
        stored = " ".join(str(cell) for row in conn.execute("SELECT * FROM api_usage") for cell in row)
    finally:
        conn.close()
    assert "sk-minimax-secret" not in stored
    assert "sk-llm-secret" not in stored
    assert "sk-sf-secret" not in stored


def test_failed_call_is_listed_without_breaking_the_request(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    note_call(provider="llm", kind="chat", model="glm-5.2", payload={"usage": {"total_tokens": 1}}, ok=False)
    summary = usage_summary()
    assert summary["recent"][0]["ok"] is False
    assert summary["all_time"]["chat"]["calls"] == 1
    assert summary["all_time"]["chat"]["total_tokens"] == 1
