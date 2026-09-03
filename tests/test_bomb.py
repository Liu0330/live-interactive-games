from app.games.bomb import apply_guess, parse_guess


def test_parse_guess():
    assert parse_guess("42") == 42
    assert parse_guess(" 7 ") == 7
    assert parse_guess("奶茶") is None
    assert parse_guess("12.5") is None


def test_range_shrinks_when_too_low():
    result = apply_guess(1, 100, 37, 20)
    assert result["ok"] is True
    assert result["hint"] == "low"
    assert result["low"] == 21
    assert result["high"] == 100
    assert result["exploded"] is False


def test_range_shrinks_when_too_high():
    result = apply_guess(21, 100, 37, 80)
    assert result["hint"] == "high"
    assert result["low"] == 21
    assert result["high"] == 79


def test_hit_explodes():
    result = apply_guess(21, 79, 37, 37)
    assert result["exploded"] is True
    assert result["hint"] == "hit"


def test_out_of_range_does_not_move():
    result = apply_guess(21, 79, 37, 3)
    assert result["ok"] is False
    assert result["hint"] == "out"
    assert result["low"] == 21
    assert result["high"] == 79
