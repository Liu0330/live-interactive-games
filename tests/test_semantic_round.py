from app import db
from app.bus import ChatEvent
from app.games.semantic import SemanticGame


def test_semantic_hides_secret_until_win():
    db.init_db()
    game = SemanticGame()
    game.start_round("奶茶")
    public = game.public_state()
    assert public["reveal"] == ""
    assert "奶茶" not in (public.get("hints") or [])
    host = game.host_state()
    assert "奶茶" in host["status_text"]

    notes = game.on_comment(ChatEvent(nickname="甲", content="红茶"))
    assert "guess" in notes
    assert game.public_state()["reveal"] == ""
    assert game.public_state()["guesses"][0]["word"] == "红茶"

    notes = game.on_comment(ChatEvent(nickname="乙", content="奶茶"))
    assert "win" in notes
    assert game.public_state()["reveal"] == "奶茶"
    assert game.winner == "乙"
