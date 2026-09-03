from app.games.similarity import (
    blend_score,
    embedding_percent,
    hint_neighbors,
    is_usable_guess,
    local_similarity,
    normalize_word,
    pinyin_similarity,
    rank_guesses,
    related_boost,
)


RELATED = {
    "奶茶": {"红茶": 0.82, "奶盖": 0.88, "tea": 0.8, "珍珠": 0.78},
    "月亮": {"太阳": 0.82, "星空": 0.76},
    "太阳": {"月亮": 0.82},
    "猫咪": {"猫": 0.92},
    "猫": {"猫咪": 0.92},
    "淘气": {"调皮": 0.86, "顽皮": 0.82},
}


def test_exact_match_is_100():
    assert local_similarity("奶茶", "奶茶", RELATED) == 100.0
    assert local_similarity("  月亮 ", "月亮", RELATED) == 100.0
    assert local_similarity("淘气", "淘气", RELATED) == 100.0


def test_homophone_taoqi_is_high():
    score = local_similarity("陶器", "淘气", RELATED)
    assert score > 80
    assert 82 <= score <= 92
    assert pinyin_similarity("陶器", "淘气") >= 0.88


def test_unrelated_asad_stays_low():
    score = local_similarity("阿萨德", "淘气", RELATED)
    assert score < 20


def test_tea_pair_is_high():
    close = local_similarity("红茶", "奶茶", RELATED)
    far = local_similarity("飞机", "奶茶", RELATED)
    assert close > 70
    assert close > far + 15


def test_sun_moon_mid_high():
    score = local_similarity("太阳", "月亮", RELATED)
    assert score >= 68


def test_cat_containment():
    assert local_similarity("猫", "猫咪", RELATED) >= 72


def test_tone_only_difference_is_high_70s():
    # 买 mǎi / 卖 mài：同拼不同调
    score = local_similarity("卖", "买", {})
    assert 74 <= score <= 82


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


def test_ranking_order_homophone_then_noise():
    secret = "淘气"
    rows = []
    for i, word in enumerate(["阿萨德", "陶器", "飞机"], start=1):
        rows.append(
            {
                "nickname": word,
                "word": word,
                "score": local_similarity(word, secret, RELATED),
                "seq": i,
            }
        )
    ranked = rank_guesses(rows)
    assert ranked[0]["word"] == "陶器"
    assert ranked[0]["score"] > 80
    assert ranked[-1]["score"] < 20
    assert [r["word"] for r in ranked][0] == "陶器"


def test_related_boost_symmetric():
    assert related_boost("红茶", "奶茶", RELATED) == 0.82
    assert related_boost("奶茶", "红茶", RELATED) == 0.82


def test_embedding_percent_spreads_not_around_fifty():
    far = embedding_percent([1.0, 0.0, 0.0], [0.30, 0.9539, 0.0])
    near = embedding_percent([1.0, 0.0, 0.0], [0.70, 0.7141, 0.0])
    hit = embedding_percent([1.0, 0.0], [1.0, 0.0])
    assert 20 <= far <= 40
    assert 65 <= near <= 85
    assert hit >= 95


def test_blend_keeps_homophone_when_embed_is_low():
    local = local_similarity("陶器", "淘气", RELATED)
    assert blend_score(local, 31.0) == local
    assert blend_score(40.0, 77.0) == 77.0


def test_hints_never_use_random_junk():
    junk = ["刨冰", "头饰佩", "灯会亮", "头饰佩戴"]
    assert hint_neighbors("淘气", pool=junk, table={}) == []
    hints = hint_neighbors("淘气", pool=junk, table=RELATED)
    assert "调皮" in hints
    assert "顽皮" in hints
    assert "刨冰" not in hints
    assert "头饰佩" not in hints
    tea_hints = hint_neighbors("奶茶", pool=["刨冰", "红茶", "飞机"], table=RELATED)
    assert "红茶" in tea_hints
    assert "刨冰" not in tea_hints
