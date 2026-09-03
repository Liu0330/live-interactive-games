from app.games.wordbank import HEADER, classify_word, load_words, pick_word
from app.paths import QUESTIONS_PATH, WORDPOOL_PATH


def test_wordpool_header_and_size():
    first = WORDPOOL_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert first.strip() == HEADER
    words = load_words()
    assert len(words) >= 200
    assert "天空" in words
    assert all("|" not in w for w in words)


def test_pick_specified_and_classify():
    assert pick_word("巧夺天工") == "巧夺天工"
    assert classify_word("巧夺天工") == "四字词语"
    assert classify_word("奶茶") == "名词"


def test_question_bank_has_thirty():
    import json

    items = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    assert len(items) >= 30
    assert all(q.get("question") and q.get("answer") for q in items)
