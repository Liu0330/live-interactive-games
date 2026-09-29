import time
from copy import deepcopy

import pytest

from app.bus import ChatEvent
from app.config import DEFAULT_CONFIG, save_config
from app.db import clear_leaderboard, get_user, init_db
from app.games.emoji_bank import all_puzzles, delete_puzzle, generate_with_model
from app.games.emoji_guess import EmojiGame, loose_text
from app.games.idiom import IdiomGame, judge_idiom
from app.games.idiom_bank import bundled_idioms, continuations, links, remember_idiom, reset_idiom_cache
from app.scoring import account_id


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    db = tmp_path / "board.db"
    monkeypatch.setattr("app.db.DB_PATH", db)
    monkeypatch.setattr("app.games.idiom_bank.DB_PATH", db)
    monkeypatch.setattr("app.games.emoji_bank.DB_PATH", db)
    monkeypatch.setattr("app.config.CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("app.config._cache", None)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    reset_idiom_cache()
    init_db()
    import app.engagement as engagement_mod
    from app.games.manager import manager

    previous_game = manager.active_id
    previous_engagement = manager.engagement
    engagement_mod._engagement = None
    fresh = engagement_mod.get_engagement()
    manager.engagement = fresh
    yield
    manager.active_id = previous_game
    manager.engagement = previous_engagement
    engagement_mod._engagement = previous_engagement
    reset_idiom_cache()


def _chat(nickname: str, content: str, user_id: str = "") -> ChatEvent:
    return ChatEvent(nickname=nickname, content=content, user_id=user_id)


def test_bundled_idioms_keep_only_four_characters():
    words = set(bundled_idioms())
    assert words
    assert all(len(word) == 4 for word in words)
    assert {"一心一意", "意气风发", "法不责众", "马马虎虎"} <= words
    assert "意在笔先" not in words


def test_links_exact_character_and_optional_pinyin():
    assert links("一心一意", "意气风发", False)
    assert not links("意气风发", "法不责众", False)
    assert links("意气风发", "法不责众", True)


def test_chain_scores_rejects_repeat_and_offers_new_head_on_timeout(isolated):
    game = IdiomGame()
    game.start_round("一心一意")
    assert game.head == "一心一意"
    assert game.public_state()["reveal"] == ""
    game.on_comment(_chat("甲", "意气风发", "dy-1"))
    assert game.head == "意气风发"
    assert get_user("dy-1")["points"] == 30
    assert get_user("dy-1")["streak"] == 1
    before = get_user("dy-1")["points"]
    game.on_comment(_chat("甲", "意气风发", "dy-1"))
    assert "用过" in game.announcement
    assert get_user("dy-1")["points"] == before
    old = game.head
    game.ends_at = time.time() - 1
    notes = game.tick()
    assert "timeout" in notes
    assert game.status == "playing"
    assert game.head != old
    assert game.head not in {"", old}


def test_pinyin_option_and_hint_masks_a_real_continuation(isolated):
    strict = IdiomGame()
    strict.start_round("意气风发")
    strict.on_comment(_chat("乙", "法不责众", "dy-2"))
    assert "接不上" in strict.announcement
    assert get_user("dy-2")["points"] == 0

    save_config({"idiom": {"allow_pinyin": True}})
    loose = IdiomGame()
    loose.start_round("意气风发")
    loose.on_comment(_chat("乙", "法不责众", "dy-2"))
    assert loose.head == "法不责众"
    assert get_user("dy-2")["streak"] == 1

    hinted = IdiomGame()
    hinted.start_round("一心一意")
    mask = hinted.unlock_hint("丙")
    assert "□" in mask
    stem = mask.replace("□", "")
    options = continuations("一心一意", False, hinted.used)
    assert any(word.startswith(stem) for word in options)
    assert mask != options[0]


def test_unknown_idiom_waits_for_model_and_stale_token_does_not_score(isolated, monkeypatch):
    monkeypatch.setattr("app.llm.chat_completion", lambda *args, **kwargs: "no")
    assert judge_idiom("意在笔先") is False

    monkeypatch.setattr("app.llm.chat_completion", lambda *args, **kwargs: "yes")
    assert judge_idiom("意在笔先") is True

    game = IdiomGame()
    game.start_round("一心一意")
    token = game.link_token
    game.on_comment(_chat("丁", "意在笔先", "dy-3"))
    assert "核对" in game.announcement
    assert get_user("dy-3")["points"] == 0
    assert game.accept_late("意在笔先", "dy-3", "丁", token) is False

    remember_idiom("意在笔先")
    assert game.accept_late("意在笔先", "dy-3", "丁", token) is True
    assert game.head == "意在笔先"
    scored = get_user("dy-3")["points"]
    assert scored == 30

    game.skip()
    assert game.link_token != token
    assert game.accept_late("意在笔先", "dy-3", "丁", token) is False
    assert get_user("dy-3")["points"] == scored


def test_emoji_normalizes_answers_and_hides_them_until_reveal(isolated):
    assert loose_text("我觉得是月亮代表我的心！") == "月亮代表我的心"
    assert loose_text("月亮 代表 我的心") == "月亮代表我的心"

    game = EmojiGame()
    game.start_round("月亮代表我的心")
    assert game.puzzle["answer"] == "月亮代表我的心"
    assert game.public_state()["reveal"] == ""
    assert "answer" not in game.public_state()
    assert game.host_state()["answer"] == "月亮代表我的心"
    game.on_comment(_chat("戊", "我觉得是月亮代表我的心！", "dy-4"))
    assert game.status == "reveal"
    assert game.public_state()["reveal"] == "月亮代表我的心"
    assert get_user("dy-4")["points"] == 80

    save_config({"emoji": {"allow_pinyin": False}})
    strict = EmojiGame()
    strict.start_round("马马虎虎")
    strict.on_comment(_chat("己", "码码虎虎", "dy-5"))
    assert strict.status == "playing"

    save_config({"emoji": {"allow_pinyin": True}})
    loose = EmojiGame()
    loose.start_round("马马虎虎")
    for _ in range(3):
        loose.unlock_hint("己")
    shown = " ".join(loose.hints)
    assert "马马虎虎" not in shown
    assert loose.public_state()["reveal"] == ""
    loose.on_comment(_chat("己", "码码虎虎", "dy-5"))
    assert loose.status == "reveal"


def test_emoji_gift_hint_and_generated_puzzle_can_be_deleted(isolated, monkeypatch):
    game = EmojiGame()
    game.start_round("守株待兔")
    import app.engagement as engagement_mod

    eng = engagement_mod.get_engagement()
    eng.on_gift(game, ChatEvent(nickname="庚", content="小心心", event_type="gift", gift_name="小心心", gift_count=1))
    assert game.hints
    assert "守株待兔" not in " ".join(game.hints)

    tiers = deepcopy(DEFAULT_CONFIG["gift_tiers"])
    for tier in tiers:
        if tier["id"] == "big":
            tier["action"] = "skip"
    save_config({"gift_tiers": tiers})
    chain = IdiomGame()
    chain.start_round("一心一意")
    first = chain.head
    eng.on_gift(chain, ChatEvent(nickname="庚", content="鲜花", event_type="gift", gift_name="鲜花", gift_count=1, gift_value=20))
    assert chain.head != first
    assert chain.status == "playing"

    monkeypatch.setattr("app.config.chat_ready", lambda cfg=None: True)

    def fake_json(prompt, system, temperature=0.6):
        return [
            {"emojis": "🚫", "answer": "色情内容", "hint": "不要", "category": "idiom"},
            {"emojis": "🐱🐟", "answer": "猫吃鱼虾", "hint": "四个字", "category": "idiom"},
        ]

    monkeypatch.setattr("app.llm.chat_json", fake_json)
    saved = generate_with_model(2, "idiom")
    assert [item["answer"] for item in saved] == ["猫吃鱼虾"]
    puzzle_id = saved[0]["id"]
    assert any(item["id"] == puzzle_id for item in all_puzzles())
    delete_puzzle(puzzle_id)
    assert all(item["id"] != puzzle_id for item in all_puzzles())

    bundled = next(item for item in all_puzzles() if item["answer"] == "守株待兔")
    delete_puzzle(bundled["id"])
    assert all(item["id"] != bundled["id"] for item in all_puzzles())


def test_points_are_global_until_explicit_reset(isolated):
    assert account_id("", "路人甲") == "路人甲"
    assert account_id("  uid-9 ", "路人甲") == "uid-9"

    idiom = IdiomGame()
    idiom.start_round("一心一意")
    idiom.on_comment(_chat("甲", "意气风发", "dy-9"))
    idiom.on_comment(_chat("甲", "发人深省", "dy-9"))
    emoji = EmojiGame()
    emoji.start_round("马马虎虎")
    emoji.on_comment(_chat("甲", "马马虎虎", "dy-9"))
    user = get_user("dy-9")
    # 接龙 30、接龙 30+连击15、看图猜 80+连击30，三连跨玩法累加
    assert user["points"] == 30 + 45 + 110
    assert user["streak"] == 3
    assert user["best_streak"] == 3
    points = user["points"]

    from app.games.manager import manager

    manager.switch("emoji")
    manager.start_round("守株待兔")
    init_db()
    assert get_user("dy-9")["points"] == points
    assert manager.active_id == "emoji"

    manager.switch("idiom")
    manager.start_round("一心一意")
    assert get_user("dy-9")["points"] == points

    clear_leaderboard()
    assert get_user("dy-9")["points"] == 0
    assert get_user("dy-9")["exists"] is False
