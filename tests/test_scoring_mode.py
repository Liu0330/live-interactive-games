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


def test_scoring_mode_llm_related_without_siliconflow():
    info = scoring_info(
        {
            **DEFAULT_CONFIG,
            "siliconflow_api_key": "",
            "llm_base_url": "https://example.test/v1",
            "llm_api_key": "sk-llm",
            "llm_model": "glm-5.2",
        }
    )
    assert info["has_api_key"] is False
    assert info["has_llm"] is True
    assert info["scoring_mode"] == "llm_related"
    assert info["scoring_mode_label"] == "大模型相关词"


def test_scoring_mode_minimax_beats_other_providers():
    info = scoring_info(
        {
            **DEFAULT_CONFIG,
            "minimax_api_key": "sk-mm",
            "minimax_base_url": "https://api.minimaxi.com",
            "siliconflow_api_key": "sk-sf",
            "llm_base_url": "https://example.test/v1",
            "llm_api_key": "sk-llm",
        }
    )
    assert info["scoring_mode"] == "minimax_embed"
    assert info["scoring_mode_label"] == "MiniMax 向量"
    assert info["has_minimax"] is True
    assert info["voice_mode"] == "minimax"
    assert info["voice_mode_label"] == "MiniMax"


def test_scoring_mode_siliconflow_wins_when_both_configured():
    info = scoring_info(
        {
            **DEFAULT_CONFIG,
            "siliconflow_api_key": "sk-sf",
            "llm_base_url": "https://example.test/v1",
            "llm_api_key": "sk-llm",
        }
    )
    assert info["scoring_mode"] == "siliconflow_embed"
    assert info["has_api_key"] is True
    assert info["has_llm"] is True
