from app.config import DEFAULT_CONFIG, scoring_info


def test_scoring_mode_without_key():
    info = scoring_info({**DEFAULT_CONFIG, "siliconflow_api_key": ""})
    assert info["has_api_key"] is False
    assert info["scoring_mode"] == "local_pinyin"
    assert info["scoring_mode_label"] == "本地拼音+字面"


def test_scoring_mode_with_key():
    info = scoring_info({**DEFAULT_CONFIG, "siliconflow_api_key": "sk-test"})
    assert info["has_api_key"] is True
    assert info["scoring_mode"] == "siliconflow_embed"
    assert info["scoring_mode_label"] == "硅基流动向量"
