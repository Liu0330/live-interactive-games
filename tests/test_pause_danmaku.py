import time

import pytest

from app.bus import ChatEvent, bus
from app.clock import is_paused, reset as reset_clock
from app.config import danmaku_settings, save_config
from app.db import get_points, init_db, meta_get, meta_set


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
    init_db()
    from app.games.manager import manager

    if manager.paused:
        manager.resume()
    reset_clock()
    manager.paused = False
    previous = manager.active_id
    pending = manager.engagement.pending_hints
    manager.engagement.pending_hints = 0
    manager.feed.clear()
    manager.held_chat.clear()
    save_config({"auto_continue": True, "intermission_seconds": 8, "count_intermission_chat": True})
    yield manager
    if manager.paused:
        manager.resume()
    reset_clock()
    manager.paused = False
    meta_set("paused", "0")
    manager.active_id = previous
    manager.engagement.pending_hints = pending
    manager.feed.clear()
    manager.held_chat.clear()


def test_pause_freezes_countdown_and_blocks_auto_continue(room):
    room.switch("semantic")
    room.start_round("春卷")
    room.pause()
    try:
        frozen = room.game.remaining()
        first = room.snapshot()
        assert first["paused"] is True
        assert meta_get("paused") == "1"
        time.sleep(1.2)
        assert room.tick() == []
        assert room.game.status == "playing"
        assert room.game.remaining() == frozen
        again = room.snapshot()
        assert again["paused"] is True
        assert again["countdown"] == first["countdown"]
        room.game.status = "reveal"
        room.game.reveal_until = time.time() - 5
        assert room.tick() == []
        assert room.game.status == "reveal"
    finally:
        room.resume()
    assert room.snapshot()["paused"] is False
    assert meta_get("paused") == "0"
    room.tick()
    assert room.game.status == "playing"


def test_resume_continues_from_remaining_then_can_timeout(room):
    room.switch("quiz")
    room.start_round("")
    room.pause()
    try:
        frozen = room.game.remaining()
        time.sleep(1.1)
        assert room.game.remaining() == frozen
    finally:
        room.resume()
    assert abs(room.game.remaining() - frozen) <= 1
    room.game.ends_at = time.time() - 1
    room.tick()
    assert room.game.status == "reveal"


def test_round_started_while_paused_keeps_its_countdown(room):
    room.pause()
    try:
        room.switch("quiz")
        room.start_round("")
        frozen = room.game.remaining()
        assert frozen >= 59
        time.sleep(1.1)
        assert room.tick() == []
        assert room.game.remaining() == frozen
    finally:
        room.resume()
    assert abs(room.game.remaining() - frozen) <= 1


def test_chat_shows_but_does_not_score_while_paused(room):
    room.switch("semantic")
    room.start_round("春卷")
    room.pause()
    try:
        bus.publish(ChatEvent(nickname="甲", content="春卷", user_id="pause-user"))
        bus.publish(ChatEvent(nickname="甲", content="", event_type="gift", gift_name="小心心", gift_count=1, user_id="pause-user"))
        snap = room.snapshot()
        texts = {(item["kind"], item["text"]) for item in snap["feed"]}
        assert ("chat", "春卷") in texts
        assert ("gift", "送出 小心心×1") in texts
        assert room.game.status == "playing"
        assert room.game.hints == []
        assert room.engagement.pending_hints == 0
        assert get_points("pause-user") == 0
    finally:
        room.resume()


def test_correct_answer_is_marked_on_the_feed(room):
    room.switch("quiz")
    room.start_round("")
    answer = room.game.question["answer"]
    bus.publish(ChatEvent(nickname="乙", content=answer, user_id="win-user"))
    marked = [item for item in room.feed if item.get("text") == answer]
    assert marked
    assert marked[-1]["style"] == "win"
    assert get_points("win-user") > 0


def test_pause_flag_survives_restore(room):
    room.switch("bomb")
    room.start_round("40")
    meta_set("paused", "1")
    assert room.paused is False
    room.restore_pause()
    try:
        assert room.paused is True
        assert is_paused()
        first = room.snapshot()["countdown"]
        time.sleep(1.1)
        second = room.snapshot()
        assert second["paused"] is True
        assert second["countdown"] == first
    finally:
        room.resume()


def test_danmaku_settings_are_clamped(room):
    save_config(
        {
            "danmaku": {
                "enabled": "false",
                "speed": 1,
                "font_size": 999,
                "opacity": 0,
                "lanes": 99,
                "per_second": 0,
                "band_top": 90,
                "band_height": 2,
            }
        }
    )
    got = danmaku_settings()
    assert got == {
        "enabled": False,
        "speed": 4,
        "font_size": 72,
        "opacity": 0.25,
        "lanes": 12,
        "per_second": 1,
        "band_top": 70,
        "band_height": 10,
    }
    assert room.snapshot()["danmaku"]["enabled"] is False
