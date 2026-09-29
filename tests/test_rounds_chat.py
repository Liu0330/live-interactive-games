import time

import pytest

from app.bus import ChatEvent, bus
from app.config import save_config
from app.db import init_db
from app.games.idiom_bank import all_idioms, reset_idiom_cache


@pytest.fixture
def room(tmp_path, monkeypatch):
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
    from app.games.manager import manager

    previous = manager.active_id
    pending = manager.engagement.pending_hints
    manager.engagement.pending_hints = 0
    manager.feed.clear()
    manager.held_chat.clear()
    save_config({"auto_continue": True, "intermission_seconds": 8, "count_intermission_chat": True})
    yield manager
    manager.active_id = previous
    manager.engagement.pending_hints = pending
    manager.feed.clear()
    manager.held_chat.clear()
    reset_idiom_cache()


def _finish_intermission(manager) -> None:
    game = manager.game
    assert game.status == "reveal"
    game.reveal_until = time.time() - 1
    manager.tick()


def test_timeout_and_win_start_the_next_round(room):
    cases = [
        ("semantic", "淘气"),
        ("quiz", ""),
        ("bomb", ""),
        ("lottery", ""),
        ("emoji", "马马虎虎"),
    ]
    for game_id, specified in cases:
        room.switch(game_id)
        room.start_round(specified)
        game = room.game
        started = game.round_no
        game.ends_at = time.time() - 1
        room.tick()
        assert game.status == "reveal", game_id
        assert game.announcement
        _finish_intermission(room)
        assert game.status == "playing", game_id
        assert game.round_no == started + 1

    room.switch("emoji")
    room.start_round("守株待兔")
    game = room.game
    started = game.round_no
    bus.publish(ChatEvent(nickname="甲", content="守株待兔", user_id="dy-win"))
    assert game.status == "reveal"
    assert "守株待兔" in game.announcement
    assert any(item["kind"] == "chat" and item["text"] == "守株待兔" for item in room.snapshot()["feed"])
    _finish_intermission(room)
    assert game.status == "playing"
    assert game.round_no == started + 1


def test_auto_continue_can_be_turned_off_and_skip_stays_idle(room):
    save_config({"auto_continue": False})
    room.switch("emoji")
    room.start_round("马马虎虎")
    game = room.game
    started = game.round_no
    game.ends_at = time.time() - 1
    room.tick()
    assert game.status == "reveal"
    game.reveal_until = time.time() - 1
    room.tick()
    assert game.status == "idle"
    assert game.round_no == started
    room.tick()
    assert game.status == "idle"

    save_config({"auto_continue": True})
    room.start_round("马马虎虎")
    room.skip()
    assert game.status == "idle"
    room.tick()
    assert game.status == "idle"


def test_several_rounds_keep_cycling(room):
    room.switch("emoji")
    room.start_round("马马虎虎")
    game = room.game
    started = game.round_no
    for step in range(1, 5):
        game.ends_at = time.time() - 1
        room.tick()
        assert game.status == "reveal"
        game.reveal_until = time.time() - 1
        room.tick()
        assert game.status == "playing"
        assert game.round_no == started + step


def test_idiom_timeout_continues_chain_and_exhausted_round_restarts(room):
    room.switch("idiom")
    room.start_round("一心一意")
    game = room.game
    old = game.head
    game.ends_at = time.time() - 1
    room.tick()
    assert game.status == "playing"
    assert game.head != old

    game.used = set(all_idioms())
    game.ends_at = time.time() - 1
    started = game.round_no
    room.tick()
    assert game.status == "playing"
    assert game.round_no == started + 1
    assert game.head


def test_idle_and_intermission_chat_is_visible_and_can_count_next_round(room):
    room.switch("emoji")
    room.start_round("守株待兔")
    game = room.game
    game.ends_at = time.time() - 1
    room.tick()
    assert game.status == "reveal"
    bus.publish(ChatEvent(nickname="丙", content="马马虎虎", user_id="dy-gap"))
    snap = room.snapshot()
    assert any(item["kind"] == "chat" and item["text"] == "马马虎虎" and item["nickname"] == "丙" for item in snap["feed"])
    assert room.held_chat

    room.start_round("马马虎虎")
    assert game.winner == "丙"
    assert game.status == "reveal"
    assert "马马虎虎" in game.announcement

    save_config({"count_intermission_chat": False, "auto_continue": False})
    game.reveal_until = time.time() - 1
    room.tick()
    assert game.status == "idle"
    room.held_chat.clear()
    bus.publish(ChatEvent(nickname="丁", content="只是看看", user_id="dy-look"))
    snap = room.snapshot()
    assert any(item["text"] == "只是看看" for item in snap["feed"])
    assert room.held_chat == []


def test_feed_styles_and_rate_limit(room):
    room.switch("quiz")
    room.skip()
    bus.publish(ChatEvent(nickname="戊", content="鲜花", event_type="gift", gift_name="鲜花", gift_count=2))
    bus.publish(ChatEvent(nickname="戊", content="", event_type="like", like_count=4))
    bus.publish(ChatEvent(nickname="戊", content="", event_type="like", like_count=6))
    bus.publish(ChatEvent(nickname="己", content="", event_type="member"))
    snap = room.snapshot()
    kinds = {(item["nickname"], item["kind"], item["text"]) for item in snap["feed"]}
    assert ("戊", "gift", "送出 鲜花×2") in kinds
    assert ("戊", "like", "点赞 +10") in kinds
    assert ("己", "member", "进入直播间") in kinds

    room.feed.clear()
    for index in range(20):
        bus.publish(ChatEvent(nickname="庚", content=f"刷屏{index}", user_id=f"spam-{index}"))
    chats = [item for item in room.feed if item["kind"] == "chat"]
    assert len(chats) <= 8
    assert chats[-1]["text"] == "刷屏19"
