from app.games.similarity import (
    is_usable_guess,
    local_similarity,
    normalize_word,
    rank_guesses,
    related_boost,
)


RELATED = {
    "奶茶": {"红茶": 0.82, "奶盖": 0.88, "tea": 0.8, "珍珠": 0.78},
    "月亮": {"太阳": 0.72, "星空": 0.76},
}


def test_exact_match_is_100():
    assert local_similarity("奶茶", "奶茶", RELATED) == 100.0
    assert local_similarity("  月亮 ", "月亮", RELATED) == 100.0


def test_related_beats_unrelated():
    close = local_similarity("红茶", "奶茶", RELATED)
    far = local_similarity("飞机", "奶茶", RELATED)
    assert close > 60
    assert close > far + 15


def test_normalize_and_filter():
    assert normalize_word(" 奶 茶！") == "奶茶"
    assert is_usable_guess("红茶")
    assert not is_usable_guess("")
    assert not is_usable_guess("这是一句太长完全不像猜词的弹幕内容啊")


def test_rank_keeps_best_unique_word():
    rows = [
        {"nickname": "甲", "word": "红茶", "score": 70, "seq": 1},
        {"nickname": "乙", "word": "奶盖", "score": 88, "seq": 2},
        {"nickname": "丙", "word": "红茶", "score": 76.8, "seq": 3},
        {"nickname": "丁", "word": "飞机", "score": 12, "seq": 4},
    ]
    ranked = rank_guesses(rows)
    assert [r["word"] for r in ranked] == ["奶盖", "红茶", "飞机"]
    assert ranked[1]["score"] == 76.8
    assert ranked[1]["nickname"] == "丙"
    assert ranked[0]["place"] == 1


def test_related_boost_symmetric():
    assert related_boost("红茶", "奶茶", RELATED) == 0.82
    assert related_boost("奶茶", "红茶", RELATED) == 0.82
