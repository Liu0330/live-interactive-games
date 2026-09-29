import json
import time

import httpx

from app.bus import ChatEvent
from app.config import DEFAULT_CONFIG, api_key, load_config, public_config
from app.games.semantic import SemanticGame
from app.games.similarity import local_similarity
from app.llm import fetch_related, generate_words, parse_related_payload
from app.related_cache import combine_scores, get_related, reset_for_tests, save_related, schedule_prefetch
from app.tts import prepare_announcement


def _use_llm_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-placeholder")
    monkeypatch.setenv("LLM_MODEL", "glm-5.2")
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr("app.config._cache", None)


def _mock_chat(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr("app.llm.httpx.Client", factory)


def test_fetch_related_posts_openai_chat(monkeypatch):
    _use_llm_env(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        content = json.dumps({"words": [{"word": "调皮", "score": 90}, {"word": "淘气", "score": 100}]}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    _mock_chat(monkeypatch, handler)
    mapping = fetch_related("淘气")
    assert mapping == {"调皮": 90.0}
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-placeholder"
    assert seen["body"]["model"] == "glm-5.2"


def test_fetch_related_timeout_and_bad_json_return_none(monkeypatch):
    _use_llm_env(monkeypatch)

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    _mock_chat(monkeypatch, timeout)
    assert fetch_related("月亮") is None

    def garbage(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "不是 JSON"}}]})

    _mock_chat(monkeypatch, garbage)
    assert fetch_related("月亮") is None


def test_parse_related_accepts_fenced_and_dict_forms():
    fenced = '说明\n```json\n{"words":[{"word":"红茶","score":88}]}\n```'
    assert parse_related_payload(json.loads(fenced.split("```json")[1].split("```")[0]), "奶茶")["红茶"] == 88
    assert parse_related_payload({"红茶": 70, "奶茶": 100}, "奶茶") == {"红茶": 70.0}
    assert parse_related_payload([{"词语": "珍珠", "相关度": 66}], "奶茶")["珍珠"] == 66


def test_combine_scores_caps_synonym_unless_allowed():
    assert combine_scores(20, 96, hit_threshold=80, allow_related_win=False) == 79.5
    assert combine_scores(20, 96, hit_threshold=80, allow_related_win=True) == 96.0
    assert combine_scores(88, 40, hit_threshold=80, allow_related_win=False) == 88.0
    assert combine_scores(30, None) == 30.0


def test_related_cache_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "rel.db"
    monkeypatch.setattr("app.db.DB_PATH", db_path)
    monkeypatch.setattr("app.related_cache.DB_PATH", db_path)
    reset_for_tests()
    save_related("淘气", {"调皮": 90, "淘气": 100})
    from app import related_cache

    related_cache._mem.clear()
    loaded = get_related("淘气")
    assert loaded["调皮"] == 90.0
    assert "淘气" not in loaded


def test_synonym_does_not_win_unless_configured(monkeypatch):
    monkeypatch.setattr("app.config.minimax_ready", lambda cfg=None: False)
    monkeypatch.setattr("app.config.llm_ready", lambda cfg=None: True)
    monkeypatch.setattr(
        "app.config.load_config",
        lambda: {**DEFAULT_CONFIG, "llm_related_can_win": False},
    )

    def lookup(_secret, word):
        return {"调皮": 96.0, "陶器": 10.0, "苹果": 100.0}.get(word)

    monkeypatch.setattr("app.related_cache.lookup_score", lookup)
    monkeypatch.setattr("app.related_cache.schedule_refine", lambda *_a, **_k: None)
    game = SemanticGame()
    game.start_round("淘气")
    assert game.score_word("调皮") < 80
    assert game.score_word("陶器") > 80
    assert game.score_word("苹果") < 80
    notes = game.on_comment(ChatEvent(nickname="甲", content="调皮"))
    assert "win" not in notes
    notes = game.on_comment(ChatEvent(nickname="乙", content="苹果"))
    assert "win" not in notes
    notes = game.on_comment(ChatEvent(nickname="丙", content="淘气"))
    assert "win" in notes
    assert game.public_state()["reveal"] == "淘气"


def test_related_can_win_when_enabled(monkeypatch):
    monkeypatch.setattr("app.config.minimax_ready", lambda cfg=None: False)
    monkeypatch.setattr("app.config.llm_ready", lambda cfg=None: True)
    monkeypatch.setattr(
        "app.config.load_config",
        lambda: {**DEFAULT_CONFIG, "llm_related_can_win": True},
    )
    monkeypatch.setattr("app.related_cache.lookup_score", lambda _secret, word: 96.0 if word == "苹果" else None)
    game = SemanticGame()
    game.start_round("淘气")
    assert game.score_word("苹果") == 96.0
    notes = game.on_comment(ChatEvent(nickname="甲", content="苹果"))
    assert "win" in notes


def test_cache_miss_scores_locally_without_http(monkeypatch):
    monkeypatch.setattr("app.config.minimax_ready", lambda cfg=None: False)
    monkeypatch.setattr("app.config.llm_ready", lambda cfg=None: True)
    monkeypatch.setattr(
        "app.config.load_config",
        lambda: {**DEFAULT_CONFIG, "llm_related_can_win": False},
    )
    monkeypatch.setattr("app.related_cache.lookup_score", lambda *_a, **_k: None)
    queued = []
    monkeypatch.setattr("app.related_cache.schedule_refine", lambda secret, word: queued.append((secret, word)))

    def forbid_http(*_a, **_k):
        raise AssertionError("弹幕计分不应发起 HTTP")

    monkeypatch.setattr("app.llm.httpx.Client", forbid_http)
    game = SemanticGame()
    game.start_round("月亮")
    started = time.perf_counter()
    score = game.score_word("太阳")
    assert time.perf_counter() - started < 0.2
    assert score == local_similarity("太阳", "月亮")
    assert queued == [("月亮", "太阳")]


def test_prefetch_returns_before_model(monkeypatch):
    monkeypatch.setattr("app.related_cache.llm_ready", lambda cfg=None: True)
    monkeypatch.setattr("app.related_cache.get_related", lambda _secret: {})
    state = {"done": False}

    def slow(_secret):
        time.sleep(0.6)
        state["done"] = True
        return {"太阳": 80.0}

    monkeypatch.setattr("app.related_cache.fetch_related", slow)
    monkeypatch.setattr("app.related_cache.save_related", lambda *_a, **_k: None)
    reset_for_tests()
    started = time.perf_counter()
    schedule_prefetch("月亮")
    assert time.perf_counter() - started < 0.25
    deadline = time.perf_counter() + 3
    while not state["done"] and time.perf_counter() < deadline:
        time.sleep(0.05)
    assert state["done"] is True


def test_absorb_related_feeds_hint_pool(monkeypatch):
    monkeypatch.setattr("app.related_cache.get_related", lambda _secret: {})
    game = SemanticGame()
    game.start_round("奶茶")
    game.hint_pool = ["旧提示"]
    game.absorb_related({"红茶": 90, "珍珠": 40, "奶茶": 100})
    assert game.hint_pool[0] == "红茶"
    assert "珍珠" not in game.hint_pool[:1]


def test_generate_words_uses_llm_when_ready(monkeypatch):
    monkeypatch.setattr("app.llm.llm_ready", lambda cfg=None: True)
    monkeypatch.setattr("app.llm.chat_json", lambda *_a, **_k: ["苹果", "香蕉"])

    def forbid(*_a, **_k):
        raise AssertionError("不应调用硅基流动")

    monkeypatch.setattr("app.siliconflow.generate_words", forbid)
    assert generate_words(2, "水果") == ["苹果", "香蕉"]


def test_public_config_hides_llm_key():
    cfg = {
        **DEFAULT_CONFIG,
        "llm_base_url": "https://example.test/v1",
        "llm_api_key": "sk-secret-key-1234",
        "siliconflow_api_key": "",
    }
    pub = public_config(cfg)
    blob = json.dumps(pub, ensure_ascii=False)
    assert "llm_api_key" not in pub
    assert "sk-secret-key-1234" not in blob
    assert pub["llm_api_key_masked"].endswith("1234")
    assert pub["scoring_mode"] == "llm_related"


def test_env_llm_key_stays_off_siliconflow_and_browser_voice(monkeypatch):
    _use_llm_env(monkeypatch)
    cfg = load_config()
    assert cfg["llm_api_key"] == "sk-placeholder"
    assert cfg["llm_model"] == "glm-5.2"
    assert api_key() != "sk-placeholder"
    monkeypatch.setattr("app.tts.api_key", lambda: "")
    spoken = prepare_announcement("谢谢小明")
    assert spoken["use_browser"] is True
    assert spoken["audio_url"] == ""
