import json
import time

import httpx

from app.bus import ChatEvent
from app.config import DEFAULT_CONFIG, load_config, public_config, save_config
from app.embed_cache import (
    _run_batch,
    get_vector,
    lookup_percent,
    reset_for_tests,
    save_vectors,
    schedule_secret,
)
from app.games.semantic import SemanticGame
from app.games.similarity import embedding_percent, local_similarity
from app.llm import chat_completion
from app.minimax import complete_chat, embed_texts, strip_think, synthesize_speech
from app.related_cache import combine_scores
from app.tts import prepare_announcement


def _use_minimax_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-replace-me")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com")
    monkeypatch.setenv("MINIMAX_CHAT_MODEL", "MiniMax-M3")
    monkeypatch.setenv("MINIMAX_EMBED_MODEL", "embo-01")
    monkeypatch.setenv("MINIMAX_TTS_MODEL", "speech-02-turbo")
    monkeypatch.setenv("MINIMAX_TTS_VOICE", "male-qn-qingse")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.setattr("app.config._cache", None)


def _mock_http(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr("app.minimax.httpx.Client", factory)
    return factory


def test_strip_think_keeps_the_answer():
    raw = "<think>先想一下</think>\n{\"words\":[{\"word\":\"苹果\",\"score\":90}]}"
    assert strip_think(raw) == '{"words":[{"word":"苹果","score":90}]}'


def test_chat_uses_anthropic_endpoint_and_strips_think(monkeypatch):
    _use_minimax_env(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "<think>推理</think>pong"}]},
        )

    _mock_http(monkeypatch, handler)
    assert complete_chat([{"role": "user", "content": "ping"}]) == "pong"
    assert seen["url"] == "https://api.minimaxi.com/anthropic/v1/messages"
    assert seen["key"] == "sk-replace-me"
    assert seen["version"] == "2023-06-01"
    assert seen["body"]["model"] == "MiniMax-M3"


def test_generic_openai_reply_also_strips_think(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-placeholder")
    monkeypatch.setenv("LLM_MODEL", "glm-5.2")
    monkeypatch.setattr("app.config._cache", None)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.test/v1/chat/completions"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "<think>隐藏</think>{\"ok\":true}"}}]},
        )

    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr("app.llm.httpx.Client", factory)
    assert chat_completion([{"role": "user", "content": "hi"}]) == '{"ok":true}'


def test_embeddings_use_db_and_query_types(monkeypatch):
    _use_minimax_env(monkeypatch)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        assert request.headers.get("authorization") == "Bearer sk-replace-me"
        assert str(request.url) == "https://api.minimaxi.com/v1/embeddings"
        return httpx.Response(200, json={"vectors": [[0.1, 0.2] for _ in body["texts"]]})

    _mock_http(monkeypatch, handler)
    assert embed_texts(["淘气"], "db") == [[0.1, 0.2]]
    assert embed_texts(["苹果"], "query") == [[0.1, 0.2]]
    assert seen[0]["type"] == "db"
    assert seen[0]["model"] == "embo-01"
    assert seen[1]["type"] == "query"


def test_embedding_timeout_returns_none(monkeypatch):
    _use_minimax_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    _mock_http(monkeypatch, handler)
    assert embed_texts(["月亮"], "db") is None


def test_tts_decodes_hex_mp3(monkeypatch):
    _use_minimax_env(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        seen["auth"] = request.headers.get("authorization")
        assert str(request.url) == "https://api.minimaxi.com/v1/t2a_v2"
        return httpx.Response(
            200,
            json={
                "data": {"audio": "494433"},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    _mock_http(monkeypatch, handler)
    assert synthesize_speech("感谢小明") == bytes.fromhex("494433")
    assert seen["auth"] == "Bearer sk-replace-me"
    assert seen["body"]["model"] == "speech-02-turbo"
    assert seen["body"]["voice_setting"]["voice_id"] == "male-qn-qingse"
    assert seen["body"]["voice_setting"]["speed"] == 0.92
    assert seen["body"]["voice_setting"]["emotion"] == "happy"
    assert seen["body"]["audio_setting"]["format"] == "mp3"


def test_tts_rejects_nonzero_status(monkeypatch):
    _use_minimax_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"audio": "00"}, "base_resp": {"status_code": 1004, "status_msg": "voice missing"}},
        )

    _mock_http(monkeypatch, handler)
    try:
        synthesize_speech("你好")
    except Exception as exc:
        assert "voice missing" in str(exc)
        return
    raise AssertionError("失败状态应该抛出")


def test_embedding_score_is_capped_unless_allowed():
    perfect = embedding_percent([1.0, 0.0], [1.0, 0.0])
    assert perfect > 80
    assert combine_scores(20, perfect, hit_threshold=80, allow_related_win=False) == 79.5
    assert combine_scores(88, 40, hit_threshold=80, allow_related_win=False) == 88.0
    assert combine_scores(20, perfect, hit_threshold=80, allow_related_win=True) == perfect


def test_embed_cache_roundtrip_and_batch(tmp_path, monkeypatch):
    db_path = tmp_path / "embed.db"
    monkeypatch.setattr("app.db.DB_PATH", db_path)
    monkeypatch.setattr("app.embed_cache.DB_PATH", db_path)
    calls = []

    def fake(texts, kind):
        calls.append((kind, list(texts)))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("app.embed_cache.embed_texts", fake)
    reset_for_tests()
    _run_batch("query", ["太阳", "星空"])
    _run_batch("db", ["月亮"])
    from app import embed_cache

    embed_cache._mem.clear()
    assert get_vector("db", "月亮") == [1.0, 0.0]
    assert get_vector("query", "太阳") == [1.0, 0.0]
    assert lookup_percent("月亮", "太阳") == embedding_percent([1.0, 0.0], [1.0, 0.0])
    assert calls == [("query", ["太阳", "星空"]), ("db", ["月亮"])]


def test_minimax_synonym_does_not_autowin(tmp_path, monkeypatch):
    db_path = tmp_path / "embed.db"
    monkeypatch.setattr("app.db.DB_PATH", db_path)
    monkeypatch.setattr("app.embed_cache.DB_PATH", db_path)
    monkeypatch.setattr("app.config.minimax_ready", lambda cfg=None: True)
    monkeypatch.setattr(
        "app.config.load_config",
        lambda: {**DEFAULT_CONFIG, "llm_related_can_win": False, "minimax_embed_model": "embo-01"},
    )
    reset_for_tests()
    save_vectors("db", [("淘气", [1.0, 0.0])])
    save_vectors("query", [("苹果", [1.0, 0.0])])
    queued = []
    monkeypatch.setattr("app.embed_cache.schedule_guess", lambda secret, word: queued.append((secret, word)))

    def forbid(*_args, **_kwargs):
        raise AssertionError("弹幕计分不应发起 HTTP")

    monkeypatch.setattr("app.minimax.httpx.Client", forbid)
    game = SemanticGame()
    game.start_round("淘气")
    assert game.score_word("苹果") < 80
    assert game.score_word("陶器") > 80
    assert queued == [("淘气", "陶器")]
    notes = game.on_comment(ChatEvent(nickname="甲", content="苹果"))
    assert "win" not in notes
    notes = game.on_comment(ChatEvent(nickname="乙", content="陶器"))
    assert "win" in notes
    assert game.winner == "乙"


def test_public_config_and_env_names(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("app.config._cache", None)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    save_config(
        {
            "minimax_api_key": "sk-replace-me",
            "minimax_base_url": "https://api.minimaxi.com",
            "minimax_chat_model": "MiniMax-M3",
            "minimax_embed_model": "embo-01",
            "minimax_tts_model": "speech-02-turbo",
            "minimax_tts_voice": "male-qn-qingse",
        }
    )
    pub = public_config()
    blob = json.dumps(pub, ensure_ascii=False)
    assert "minimax_api_key" not in pub
    assert "sk-replace-me" not in blob
    assert pub["scoring_mode"] == "minimax_embed"
    assert pub["minimax_chat_model"] == "MiniMax-M3"
    save_config({"minimax_tts_voice": "female-shaonv"})
    assert load_config()["minimax_api_key"] == "sk-replace-me"
    assert load_config()["minimax_tts_voice"] == "female-shaonv"


def test_announcement_prefers_minimax_audio(monkeypatch, tmp_path):
    monkeypatch.setattr("app.tts.minimax_ready", lambda cfg=None: True)
    monkeypatch.setattr("app.tts.api_key", lambda: "sk-silicon")
    monkeypatch.setattr("app.tts.TTS_DIR", tmp_path)
    monkeypatch.setattr("app.minimax.synthesize_speech", lambda text: b"ID3minimax")

    def forbid(*_args, **_kwargs):
        raise AssertionError("不应调用硅基流动语音")

    monkeypatch.setattr("app.siliconflow.synthesize_speech", forbid)
    spoken = prepare_announcement("感谢小明送出的鲜花")
    assert spoken["use_browser"] is False
    assert spoken["audio_url"].startswith("/api/tts/")
    assert (tmp_path / spoken["audio_url"].rsplit("/", 1)[-1]).read_bytes() == b"ID3minimax"


def test_schedule_secret_returns_before_network(tmp_path, monkeypatch):
    db_path = tmp_path / "embed.db"
    monkeypatch.setattr("app.db.DB_PATH", db_path)
    monkeypatch.setattr("app.embed_cache.DB_PATH", db_path)
    monkeypatch.setattr("app.embed_cache.minimax_ready", lambda cfg=None: True)
    state = {"done": False}

    def slow(_texts, _kind):
        time.sleep(0.5)
        state["done"] = True
        return [[0.2, 0.4]]

    monkeypatch.setattr("app.embed_cache.embed_texts", slow)
    reset_for_tests()
    started = time.perf_counter()
    schedule_secret("奶茶")
    assert time.perf_counter() - started < 0.25
    deadline = time.perf_counter() + 2
    while not state["done"] and time.perf_counter() < deadline:
        time.sleep(0.05)
    assert state["done"] is True
