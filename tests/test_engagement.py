from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.bus import ChatEvent
from app.config import DEFAULT_CONFIG
from app.db import add_score_event, init_db, leaderboard_period, period_start
from app.engagement import match_tier
from app.games.bomb import narrow_range
from app.games.lottery import LotteryGame
from app.games.quiz import QuizGame
from app.games.semantic import SemanticGame
from app.ingest.douyin import _parse_push_payload
from app.scoring import grant_win, note_miss
from app.tts import prepare_announcement


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", tmp_path / "board.db")
    from app.paths import CONFIG_PATH
    import app.config as config_mod
    import app.engagement as engagement_mod

    existed = CONFIG_PATH.exists()
    original = CONFIG_PATH.read_text(encoding="utf-8") if existed else None
    engagement_mod._engagement = None
    init_db()
    fresh = engagement_mod.get_engagement()
    from app.games.manager import manager

    manager.engagement = fresh
    yield fresh
    engagement_mod._engagement = None
    if original is None:
        CONFIG_PATH.unlink(missing_ok=True)
    else:
        CONFIG_PATH.write_text(original, encoding="utf-8")
    config_mod._cache = None


def _gift(name, nickname="甲", value=0):
    return ChatEvent(
        nickname=nickname,
        content=name,
        event_type="gift",
        gift_name=name,
        gift_count=1,
        gift_value=value,
    )


def test_match_tier_name_beats_diamond_value():
    tiers = DEFAULT_CONFIG["gift_tiers"]
    small = match_tier("小心心", 999, tiers)
    big = match_tier("鲜花", 1, tiers)
    assert small["id"] == "small"
    assert small["action"] == "hint"
    assert big["id"] == "big"
    assert big["action"] == "add_time"
    assert match_tier("火箭", 20, tiers)["id"] == "big"
    assert match_tier("棒棒糖", 1, tiers)["id"] == "small"
    assert match_tier("神秘礼物", 0, tiers)["id"] == "small"


def test_small_gift_unlocks_hint_and_thanks(eng):
    game = SemanticGame()
    game.start_round("淘气")
    game.hint_pool = ["调皮"]
    before = game.ends_at
    notes = eng.on_gift(game, _gift("小心心", value=999))
    assert "调皮" in game.hints
    assert "感谢甲" in game.announcement
    assert "调皮" in game.announcement
    assert game.ends_at == before
    effect = eng.effects[-1]
    assert effect["kind"] == "gift"
    assert effect["nickname"] == "甲"
    assert "小心心" in effect["detail"]
    assert "announce" in notes


def test_big_gift_adds_time(eng):
    game = SemanticGame()
    game.start_round("淘气")
    before = game.ends_at
    eng.on_gift(game, _gift("鲜花"))
    assert game.ends_at >= before + 30
    assert "加时" in game.announcement
    assert "淘气" not in game.announcement


def test_big_gift_refresh_hides_new_secret(eng, monkeypatch):
    def fake_pick(specified="", answer_length=0, path=None):
        return specified or "月亮"

    monkeypatch.setattr("app.games.semantic.pick_word", fake_pick)
    game = SemanticGame()
    game.start_round("淘气")
    tiers = deepcopy(DEFAULT_CONFIG["gift_tiers"])
    tiers[1]["action"] = "refresh"
    monkeypatch.setattr("app.engagement.load_config", lambda: {**DEFAULT_CONFIG, "gift_tiers": tiers})
    detail_game = game
    eng.on_gift(detail_game, _gift("鲜花"))
    assert game.secret == "月亮"
    assert game.status == "playing"
    assert "月亮" not in game.announcement
    assert "刷新" in game.announcement
    assert "月亮" in game.host_state()["status_text"]


def test_big_gift_skip_ends_round_without_spoiling_in_thanks(eng, monkeypatch):
    tiers = deepcopy(DEFAULT_CONFIG["gift_tiers"])
    tiers[1]["action"] = "skip"
    monkeypatch.setattr("app.engagement.load_config", lambda: {**DEFAULT_CONFIG, "gift_tiers": tiers})
    game = SemanticGame()
    game.start_round("淘气")
    eng.on_gift(game, _gift("鲜花"))
    assert game.status == "idle"
    assert "跳过" in game.announcement
    assert "淘气" not in game.announcement


def test_quiz_hint_and_refresh(eng, monkeypatch):
    game = QuizGame()
    game.start_round("一年有多少个月")
    hinted = game.unlock_hint()
    assert hinted == "排除 A"
    assert 0 in game.eliminated
    assert game.public_state()["eliminated"] == [0]
    monkeypatch.setattr(
        "app.games.quiz.random.choice",
        lambda pool: next(q for q in pool if "首都" in q["question"]),
    )
    assert game.refresh_prompt() == "已刷新本题"
    assert "首都" in game.question["question"]
    assert game.eliminated == set()


def test_bomb_hint_halves_range_but_keeps_two_numbers():
    low, high, text = narrow_range(1, 100, 37)
    assert low == 1
    assert high == 50
    assert "不大于" in text
    low, high, text = narrow_range(1, 100, 80)
    assert low == 51
    assert high == 100
    assert narrow_range(5, 6, 5)[2] == ""
    game = __import__("app.games.bomb", fromlist=["BombGame"]).BombGame()
    game.start_round("37")
    hint = game.unlock_hint()
    assert hint
    assert game.secret == 37
    assert game.low <= 37 <= game.high
    assert game.high - game.low >= 1


def test_like_bar_grants_hint_and_keeps_remainder(eng):
    game = SemanticGame()
    game.start_round("淘气")
    game.hint_pool = ["调皮", "顽皮"]
    notes = eng.on_likes(game, "乙", 130)
    assert "like_reward" in notes
    assert eng.likes == 30
    assert game.hints
    assert "点赞进度已满" in game.announcement
    view = eng.public_view()
    assert view["like_bar"]["count"] == 30
    assert view["like_bar"]["target"] == 100


def test_like_bonus_doubles_points(eng, monkeypatch):
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["likes"] = {"target": 10, "reward": "bonus", "bonus_seconds": 30, "multiplier": 2}
    cfg["streak"] = {"bonus_per": 0, "max_bonus": 0}
    monkeypatch.setattr("app.engagement.load_config", lambda: cfg)
    monkeypatch.setattr("app.scoring.load_config", lambda: cfg)
    game = SemanticGame()
    game.start_round("淘气")
    eng.on_likes(game, "丙", 10)
    assert eng.multiplier() == 2
    award = grant_win("丙", "丙", 40, reason="test")
    assert award["multiplier"] == 2
    assert award["points_awarded"] == 80
    assert eng.public_view()["bonus"]["active"] is True


def test_streak_bonus_resets_after_wrong_answer(eng, monkeypatch):
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["streak"] = {"bonus_per": 15, "max_bonus": 60}
    cfg["points_per_sublevel"] = 50
    monkeypatch.setattr("app.scoring.load_config", lambda: cfg)
    monkeypatch.setattr("app.engagement.load_config", lambda: cfg)
    first = grant_win("甲", "甲", 40, reason="quiz")
    assert first["streak"] == 1
    assert first["streak_bonus"] == 0
    assert first["leveled_up"] is False
    second = grant_win("甲", "甲", 40, reason="quiz")
    assert second["streak"] == 2
    assert second["streak_bonus"] == 15
    assert second["points_awarded"] == 55
    assert second["leveled_up"] is True
    assert second["rank_name"] == "青铜4"
    note_miss("甲", "甲")
    third = grant_win("甲", "甲", 40, reason="quiz")
    assert third["streak"] == 1
    assert third["streak_bonus"] == 0


def test_quiz_wrong_option_resets_streak(eng, monkeypatch):
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["streak"] = {"bonus_per": 10, "max_bonus": 40}
    monkeypatch.setattr("app.scoring.load_config", lambda: cfg)
    monkeypatch.setattr("app.games.quiz.load_config", lambda: cfg)
    monkeypatch.setattr("app.engagement.load_config", lambda: cfg)
    game = QuizGame()
    game.start_round("一年有多少个月")
    game.on_comment(ChatEvent(nickname="甲", content="C"))
    assert game.last_award["streak"] == 1
    game.start_round("一年有多少个月")
    game.on_comment(ChatEvent(nickname="甲", content="A"))
    game.start_round("中国的首都是哪里")
    game.on_comment(ChatEvent(nickname="甲", content="B"))
    assert game.last_award["streak"] == 1
    assert game.last_award["streak_bonus"] == 0


def test_daily_and_weekly_boards_use_shanghai_time(eng):
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 30, 15, 0, tzinfo=tz)
    day_start = period_start("day", now)
    week_start = period_start("week", now)
    assert datetime.fromtimestamp(week_start, tz).weekday() == 0
    add_score_event("old", "旧人", 80, "win", day_start - 10)
    add_score_event("new", "新人", 20, "win", day_start + 10)
    add_score_event("early", "上周", 90, "win", week_start - 10)
    day_rows = leaderboard_period("day", now=now)
    week_rows = leaderboard_period("week", now=now)
    assert [row["nickname"] for row in day_rows] == ["新人"]
    assert "上周" not in [row["nickname"] for row in week_rows]
    assert "新人" in [row["nickname"] for row in week_rows]
    assert "旧人" in [row["nickname"] for row in week_rows]


def test_tts_falls_back_to_browser_without_key(monkeypatch):
    monkeypatch.setattr("app.tts.api_key", lambda: "")
    spoken = prepare_announcement("感谢甲送出的鲜花")
    assert spoken["use_browser"] is True
    assert spoken["audio_url"] == ""
    assert "鲜花" in spoken["text"]


def test_pending_hint_applies_next_round(eng):
    from app.games.manager import manager

    idle = SemanticGame()
    eng.on_gift(idle, _gift("小心心"))
    assert eng.pending_hints == 1
    assert "下回合" in idle.announcement
    manager.engagement = eng
    manager.active_id = "semantic"
    manager.games["semantic"] = SemanticGame()
    notes = manager.start_round("淘气")
    assert "announce" in notes
    assert manager.game.hints
    assert eng.pending_hints == 0


def _varint(number: int) -> bytes:
    out = bytearray()
    while True:
        piece = number & 0x7F
        number >>= 7
        if number:
            out.append(piece | 0x80)
        else:
            out.append(piece)
            return bytes(out)


def _bytes_field(field_no: int, payload: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(payload)) + payload


def _str_field(field_no: int, text: str) -> bytes:
    return _bytes_field(field_no, text.encode())


def _int_field(field_no: int, number: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(number)


def _user(nickname: str, uid: int = 9) -> bytes:
    return _int_field(1, uid) + _str_field(3, nickname)


def _frame(method: str, payload: bytes) -> bytes:
    message = _str_field(1, method) + _bytes_field(2, payload)
    body = _bytes_field(1, message) + _str_field(2, "cursor-1")
    return _bytes_field(8, body)


def test_douyin_like_member_and_gift_events():
    like = _parse_push_payload(_frame("WebcastLikeMessage", _bytes_field(2, _user("点赞君")) + _int_field(3, 6)))
    assert like["events"][0].event_type == "like"
    assert like["events"][0].like_count == 6
    assert like["events"][0].nickname == "点赞君"
    member = _parse_push_payload(_frame("WebcastMemberMessage", _bytes_field(2, _user("新朋友"))))
    assert member["events"][0].event_type == "member"
    assert member["events"][0].nickname == "新朋友"
    gift_struct = _str_field(2, "鲜花") + _int_field(12, 99)
    gift = _parse_push_payload(
        _frame("WebcastGiftMessage", _bytes_field(2, _user("土豪")) + _bytes_field(15, gift_struct) + _int_field(7, 2))
    )
    assert gift["events"][0].event_type == "gift"
    assert gift["events"][0].gift_name == "鲜花"
    assert gift["events"][0].gift_value == 99
    assert gift["events"][0].gift_count == 2


def test_lottery_skip_tier_draws_now(eng, monkeypatch):
    tiers = deepcopy(DEFAULT_CONFIG["gift_tiers"])
    tiers[1]["action"] = "skip"
    monkeypatch.setattr("app.engagement.load_config", lambda: {**DEFAULT_CONFIG, "gift_tiers": tiers})
    game = LotteryGame()
    game.start_round("抽奖")
    game.on_comment(ChatEvent(nickname="甲", content="抽奖"))
    eng.on_gift(game, _gift("鲜花", nickname="乙"))
    assert game.status == "reveal"
    assert game.winner
    assert "感谢乙" in game.announcement


def test_console_mock_endpoints(eng):
    from fastapi.testclient import TestClient

    from app.games.manager import manager
    from app.main import app

    manager.active_id = "semantic"
    with TestClient(app) as client:
        started = client.post("/api/round/start", json={"specified": "淘气"})
        assert started.status_code == 200
        gifted = client.post("/api/mock/gift", json={"nickname": "测试观众", "gift_name": "小心心", "count": 1})
        assert gifted.status_code == 200
        state = gifted.json()["state"]
        assert state["hints"]
        assert any(item["nickname"] == "测试观众" for item in state["effects"])
        assert state["tts"]["use_browser"] is True
        liked = client.post("/api/mock/like", json={"nickname": "测试观众", "count": 20})
        assert liked.status_code == 200
        assert liked.json()["state"]["like_bar"]["count"] == 20
        joined = client.post("/api/mock/member", json={"nickname": "新观众"})
        assert joined.json()["state"]["welcome"]["nickname"] == "新观众"
        filled = client.post("/api/mock/like", json={"nickname": "测试观众", "count": 80})
        body = filled.json()["state"]
        assert body["like_bar"]["count"] == 0
        assert "点赞" in (body.get("announcement") or "")
