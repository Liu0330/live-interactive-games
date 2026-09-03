from app.games.quiz import is_correct_answer, normalize_answer

Q = {
    "question": "一年有多少个月？",
    "options": ["10", "11", "12", "13"],
    "answer": "12",
    "aliases": ["十二", "十二个月"],
}


def test_letter_matches_option():
    assert is_correct_answer("C", Q) is True
    assert is_correct_answer("c", Q) is True
    assert is_correct_answer("A", Q) is False
    assert is_correct_answer("B", Q) is False


def test_exact_answer_and_alias():
    assert is_correct_answer("12", Q) is True
    assert is_correct_answer("十二", Q) is True
    assert is_correct_answer("十二个月", Q) is True
    assert is_correct_answer("11", Q) is False


def test_normalize_answer():
    assert normalize_answer(" 十二 ") == "十二"
    assert normalize_answer("c") == "C"
