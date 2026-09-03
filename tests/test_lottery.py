from app.games.lottery import pick_weighted, unique_participants


def test_unique_by_user_keeps_first_comment():
    entries = [
        {"user_id": "u1", "nickname": "甲", "weight": 1, "text": "抽奖"},
        {"user_id": "u2", "nickname": "乙", "weight": 1, "text": "抽奖"},
        {"user_id": "u1", "nickname": "甲", "weight": 1, "text": "抽奖再来"},
    ]
    people = unique_participants(entries)
    assert [p["user_id"] for p in people] == ["u1", "u2"]
    assert people[0]["weight"] == 1
    assert people[0]["text"] == "抽奖"


def test_gift_adds_weight_to_same_user():
    entries = [
        {"user_id": "u1", "nickname": "甲", "weight": 1, "text": "抽奖"},
        {"user_id": "u1", "nickname": "甲", "weight": 10, "text": "小心心", "gift": True},
    ]
    people = unique_participants(entries)
    assert len(people) == 1
    assert people[0]["weight"] == 11
    assert people[0]["gift"] is True


def test_weighted_pick_only_from_pool():
    people = [{"user_id": "only", "nickname": "独苗", "weight": 3}]
    winner = pick_weighted(people)
    assert winner["user_id"] == "only"
    assert pick_weighted([]) is None
