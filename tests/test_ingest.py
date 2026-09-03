from app.ingest.douyin import extract_room_token


def test_extract_room_token_from_url_and_digits():
    assert extract_room_token("https://live.douyin.com/1234567890") == "1234567890"
    assert extract_room_token("1234567890") == "1234567890"
    assert extract_room_token("  https://live.douyin.com/abc_99  ") == "abc_99"
    assert extract_room_token("") == ""
