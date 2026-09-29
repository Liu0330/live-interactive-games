"""题库可以在运行中追加，当前回合保持不变，下一回合才能抽到新内容。"""

import sqlite3

import pytest

from app.banks import (
    add_text,
    catalog,
    draft,
    export_text,
    playable_words,
    remove_item,
    save_drafts,
    update_item,
)
from app.bus import ChatEvent, bus
from app.config import save_config
from app.db import init_db
from app.games.emoji_guess import EmojiGame
from app.games.idiom import IdiomGame
from app.games.idiom_bank import all_idioms, reset_idiom_cache
from app.games.quiz import QuizGame
from app.games.semantic import SemanticGame
from app.paths import WORDPOOL_PATH


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
    yield db


def test_words_questions_idioms_and_puzzles_wait_for_the_next_round(isolated, monkeypatch):
    semantic = SemanticGame()
    semantic.start_round("天空")
    added = add_text("word", "家电|电风扇\n家电|电风扇\n色情直播", "家电")
    assert [item["word"] for item in added["added"]] == ["电风扇"]
    assert added["skipped"] == ["电风扇"]
    assert added["rejected"]
    assert semantic.secret == "天空"
    assert semantic.status == "playing"
    assert "电风扇" in playable_words()
    monkeypatch.setattr(
        "app.games.wordbank.random.choice",
        lambda words: "电风扇" if "电风扇" in words else words[0],
    )
    semantic.start_round("")
    assert semantic.secret == "电风扇"
    assert WORDPOOL_PATH.read_text(encoding="utf-8").splitlines()[0].startswith("#")
    assert "电风扇" not in WORDPOOL_PATH.read_text(encoding="utf-8")

    quiz = QuizGame()
    quiz.start_round("一年有多少个月")
    question = "自制题目吃什么？|米饭|面条|馒头|饺子|面条"
    assert add_text("question", question, "食物")["added"]
    assert "多少个月" in quiz.question["question"]
    monkeypatch.setattr(
        "app.games.quiz.random.choice",
        lambda pool: next(item for item in pool if "自制题目" in item["question"]),
    )
    quiz.start_round("")
    assert "自制题目" in quiz.question["question"]

    idiom = IdiomGame()
    idiom.start_round("一心一意")
    head = idiom.head
    assert add_text("idiom", "意在笔先", "成语")["added"]
    assert idiom.head == head
    assert idiom.status == "playing"
    assert "意在笔先" in all_idioms()

    save_config({"emoji": {"category": "idiom", "countdown": 70, "allow_pinyin": True}})
    emoji = EmojiGame()
    emoji.start_round("马马虎虎")
    assert emoji.puzzle["answer"] == "马马虎虎"
    assert add_text("puzzle", "成语|🍜🥟|自定义面|面条", "idiom")["added"]
    assert emoji.puzzle["answer"] == "马马虎虎"
    monkeypatch.setattr(
        "app.games.emoji_guess.random.choice",
        lambda pool: next(item for item in pool if item.get("answer") == "自定义面"),
    )
    emoji.start_round("")
    assert emoji.puzzle["answer"] == "自定义面"


def test_edit_search_delete_and_import_export(isolated):
    added = add_text("word", "电风扇", "家电")["added"][0]
    edited = update_item(added["id"], "", "", "", "厨房|电饭煲")
    assert edited["word"] == "电饭煲"
    assert "电饭煲" in playable_words()
    assert "电风扇" not in playable_words()
    found = catalog("word", "电饭煲", "厨房")
    assert found["total"] >= 1
    assert any(item["label"] == "电饭煲" for item in found["items"])

    text, name, _media = export_text("word", "txt")
    assert name == "word.txt"
    assert "厨房|电饭煲" in text
    csv_text, csv_name, _media = export_text("word", "csv")
    assert csv_name == "word.csv"
    assert "电饭煲" in csv_text
    remove_item(edited["id"])
    assert "电饭煲" not in playable_words()
    imported = add_text("word", csv_text, source="import")
    assert any(item["word"] == "电饭煲" for item in imported["added"])
    assert "电饭煲" in playable_words()


def test_ai_draft_is_not_saved_until_confirmed(isolated, monkeypatch):
    monkeypatch.setattr("app.config.chat_ready", lambda cfg=None: True)

    def fake_json(prompt, system, temperature=0.6):
        return [{"word": "电风扇", "category": "家电"}, {"word": "色情直播", "category": "家电"}]

    monkeypatch.setattr("app.llm.chat_json", fake_json)
    rows = draft("word", "家电", "家电", 4)
    assert [item["word"] for item in rows] == ["电风扇"]
    assert "电风扇" not in playable_words()
    saved = save_drafts("word", rows)
    assert [item["word"] for item in saved["added"]] == ["电风扇"]
    assert "电风扇" in playable_words()
    second = draft("word", "家电", "家电", 4)
    assert second[0]["duplicate"] is True
    assert save_drafts("word", second)["added"] == []


def test_viewer_suggestion_is_queued_until_approved(isolated):
    from app.banks import list_suggestions, review_suggestion
    from app.games.manager import manager

    game = manager.games["semantic"]
    manager.active_id = "semantic"
    game.start_round("天空")
    guesses = list(game.guesses)
    bus.publish(ChatEvent(nickname="甲", content="出题 #家电 电风扇", user_id="dy-1"))
    bus.publish(ChatEvent(nickname="乙", content="出题 色情直播", user_id="dy-2"))
    assert game.secret == "天空"
    assert game.guesses == guesses
    pending = list_suggestions("word")
    assert len(pending) == 1
    assert pending[0]["text"] == "电风扇"
    assert pending[0]["category"] == "家电"
    review_suggestion(pending[0]["id"], "approve")
    assert "电风扇" in playable_words()
    assert game.secret == "天空"
    conn = sqlite3.connect(isolated)
    try:
        blocked = conn.execute(
            "SELECT status FROM bank_suggestions WHERE text LIKE '%色情%'"
        ).fetchone()
    finally:
        conn.close()
    assert blocked[0] == "blocked"
