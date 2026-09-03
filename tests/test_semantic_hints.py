from app.bus import ChatEvent
from app.games.semantic import SemanticGame, _hint_candidates


def test_hint_candidates_skip_when_no_neighbors():
    assert _hint_candidates("阿萨德某某") == []


def test_taoqi_hints_are_semantic_not_junk():
    hints = _hint_candidates("淘气")
    for bad in ("刨冰", "头饰佩", "灯会亮"):
        assert bad not in hints
    assert "调皮" in hints or "顽皮" in hints


def test_game_does_not_inject_random_hints():
    game = SemanticGame()
    game.start_round("淘气")
    game.hint_pool = []
    assert game._push_hint() is False
    assert game.hints == []
    game.hint_pool = ["调皮"]
    assert game._push_hint() is True
    assert game.hints == ["调皮"]


def test_score_word_homophone_offline():
    game = SemanticGame()
    game.start_round("淘气")
    assert game.score_word("陶器") > 80
    assert game.score_word("阿萨德") < 20
    notes = game.on_comment(ChatEvent(nickname="甲", content="陶器"))
    assert "guess" in notes
    top = game.public_state()["guesses"][0]
    assert top["word"] == "陶器"
    assert top["score"] > 80
