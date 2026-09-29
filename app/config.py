from __future__ import annotations

import json
import os
from copy import deepcopy
from threading import Lock
from typing import Any

from app.paths import CONFIG_PATH, ensure_user_dirs

MINIMAX_VOICES = [
    {"id": "male-qn-qingse", "label": "青涩青年"},
    {"id": "male-qn-jingying", "label": "精英青年"},
    {"id": "female-shaonv", "label": "少女音"},
    {"id": "female-yujie", "label": "御姐音"},
    {"id": "female-chengshu", "label": "成熟女声"},
    {"id": "presenter_male", "label": "男主持人"},
    {"id": "presenter_female", "label": "女主持人"},
]

DEFAULT_CONFIG: dict[str, Any] = {
    "siliconflow_api_key": "",
    "minimax_api_key": "",
    "minimax_base_url": "https://api.minimaxi.com",
    "minimax_chat_model": "MiniMax-M3",
    "minimax_embed_model": "embo-01",
    "minimax_tts_model": "speech-02-turbo",
    "minimax_tts_voice": "presenter_female",
    "minimax_tts_speed": 0.92,
    "minimax_tts_emotion": "happy",
    "llm_base_url": "https://codingplan.alayanew.com/v1",
    "llm_api_key": "",
    "llm_model": "glm-5.2",
    "llm_related_can_win": False,
    "llm_related_count": 48,
    "chat_model": "deepseek-ai/DeepSeek-V3",
    "embed_model": "BAAI/bge-m3",
    "tts_enabled": True,
    "tts_model": "FunAudioLLM/CosyVoice2-0.5B",
    "tts_voice": "FunAudioLLM/CosyVoice2-0.5B:bella",
    "douyin_room_id": "",
    "active_game": "semantic",
    "auto_continue": True,
    "intermission_seconds": 8,
    "count_intermission_chat": True,
    "danmaku": {
        "enabled": True,
        "speed": 8,
        "font_size": 32,
        "opacity": 0.82,
        "lanes": 4,
        "per_second": 6,
        "band_top": 18,
        "band_height": 18,
    },
    "points_per_sublevel": 180,
    "rank_names": ["青铜", "白银", "黄金", "铂金", "钻石", "星耀", "王者", "挑战者"],
    "semantic": {
        "countdown": 180,
        "hit_threshold": 80.0,
        "hint_interval": 30,
        "hints_per_round": 3,
        "post_round_delay": 8,
        "answer_length": 0,
        "win_points": 120,
        "near_points": 8,
        "max_guess_chars": 12,
    },
    "quiz": {
        "countdown": 60,
        "win_points": 80,
        "post_round_delay": 6,
    },
    "bomb": {
        "min_value": 1,
        "max_value": 100,
        "countdown": 180,
        "mode": "hit",
        "win_points": 80,
        "post_round_delay": 6,
    },
    "lottery": {
        "keyword": "抽奖",
        "duration": 60,
        "gift_weight": True,
        "win_points": 50,
    },
    "idiom": {
        "link_seconds": 30,
        "allow_pinyin": False,
        "win_points": 30,
    },
    "emoji": {
        "countdown": 70,
        "category": "rotate",
        "allow_pinyin": True,
        "hint_interval": 20,
        "hints_per_round": 3,
        "win_points": 80,
        "post_round_delay": 8,
    },
    "gift_tiers": [
        {
            "id": "small",
            "label": "小礼物",
            "names": ["小心心", "玫瑰", "大啤酒", "人气票"],
            "min_value": 1,
            "max_value": 9,
            "action": "hint",
            "seconds": 30,
            "count": 30,
        },
        {
            "id": "big",
            "label": "大礼物",
            "names": ["鲜花", "你最好看", "嘉年华", "跑车"],
            "min_value": 10,
            "max_value": 0,
            "action": "add_time",
            "seconds": 30,
            "count": 30,
        },
    ],
    "likes": {
        "target": 100,
        "reward": "hint",
        "bonus_seconds": 45,
        "multiplier": 2,
    },
    "streak": {
        "bonus_per": 15,
        "max_bonus": 60,
    },
    "gifts": [
        {"name": "小心心", "action": "hint", "count": 1, "label": "小礼物 · 解锁提示"},
        {"name": "大啤酒", "action": "hint", "count": 1, "label": "小礼物 · 解锁提示"},
        {"name": "鲜花", "action": "add_time", "count": 30, "label": "大礼物 · 加时 30 秒"},
        {"name": "你最好看", "action": "refresh", "count": 1, "label": "大礼物 · 刷新题目"},
    ],
}

CHAT_MODELS = [
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-V3.1",
    "deepseek-ai/DeepSeek-V3.2",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-14B-Instruct",
    "Qwen/Qwen3-8B",
    "Qwen/Qwen3-14B",
    "Qwen/Qwen3-32B",
]

_lock = Lock()
_cache: dict[str, Any] | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return deepcopy(_cache)
        ensure_user_dirs()
        data = deepcopy(DEFAULT_CONFIG)
        if CONFIG_PATH.exists():
            try:
                disk = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(disk, dict):
                    data = _deep_merge(data, disk)
            except json.JSONDecodeError:
                pass
        env_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
        if env_key:
            data["siliconflow_api_key"] = env_key
        env_room = os.environ.get("DOUYIN_ROOM_ID", "").strip()
        if env_room:
            data["douyin_room_id"] = env_room
        env_llm_base = os.environ.get("LLM_BASE_URL", "").strip()
        if env_llm_base:
            data["llm_base_url"] = env_llm_base
        env_llm_key = os.environ.get("LLM_API_KEY", "").strip()
        if env_llm_key:
            data["llm_api_key"] = env_llm_key
        env_llm_model = os.environ.get("LLM_MODEL", "").strip()
        if env_llm_model:
            data["llm_model"] = env_llm_model
        for env_name, key in (
            ("MINIMAX_API_KEY", "minimax_api_key"),
            ("MINIMAX_BASE_URL", "minimax_base_url"),
            ("MINIMAX_CHAT_MODEL", "minimax_chat_model"),
            ("MINIMAX_EMBED_MODEL", "minimax_embed_model"),
            ("MINIMAX_TTS_MODEL", "minimax_tts_model"),
            ("MINIMAX_TTS_VOICE", "minimax_tts_voice"),
            ("MINIMAX_TTS_EMOTION", "minimax_tts_emotion"),
        ):
            env_value = os.environ.get(env_name, "").strip()
            if env_value:
                data[key] = env_value
        speed_env = os.environ.get("MINIMAX_TTS_SPEED", "").strip()
        if speed_env:
            data["minimax_tts_speed"] = speed_env
        _cache = data
        return deepcopy(data)


def save_config(partial: dict[str, Any]) -> dict[str, Any]:
    global _cache
    current = load_config()
    merged = _deep_merge(current, partial)
    ensure_user_dirs()
    disk = deepcopy(merged)
    # 磁盘上可保存密钥，但仓库不提交该文件。
    CONFIG_PATH.write_text(
        json.dumps(disk, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with _lock:
        _cache = merged
    return deepcopy(merged)


def llm_settings(cfg: dict[str, Any] | None = None) -> tuple[str, str, str]:
    data = cfg or load_config()
    base = str(data.get("llm_base_url") or "").strip().rstrip("/")
    key = str(data.get("llm_api_key") or "").strip()
    model = str(data.get("llm_model") or "").strip() or "glm-5.2"
    return base, key, model


def llm_ready(cfg: dict[str, Any] | None = None) -> bool:
    base, key, _model = llm_settings(cfg)
    return bool(base and key)


_TTS_EMOTIONS = {"happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm"}


def clamp_tts_speed(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.92
    return round(max(0.5, min(2.0, value)), 2)


def minimax_settings(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    data = cfg or load_config()
    base = str(data.get("minimax_base_url") or "https://api.minimaxi.com").strip().rstrip("/")
    emotion = str(data.get("minimax_tts_emotion") or "happy").strip().lower() or "happy"
    if emotion not in _TTS_EMOTIONS:
        emotion = "happy"
    return {
        "base_url": base or "https://api.minimaxi.com",
        "api_key": str(data.get("minimax_api_key") or "").strip(),
        "chat_model": str(data.get("minimax_chat_model") or "").strip() or "MiniMax-M3",
        "embed_model": str(data.get("minimax_embed_model") or "").strip() or "embo-01",
        "tts_model": str(data.get("minimax_tts_model") or "").strip() or "speech-02-turbo",
        "tts_voice": str(data.get("minimax_tts_voice") or "").strip() or "presenter_female",
        "tts_speed": clamp_tts_speed(data.get("minimax_tts_speed", 0.92)),
        "tts_emotion": emotion,
    }


def minimax_ready(cfg: dict[str, Any] | None = None) -> bool:
    settings = minimax_settings(cfg)
    return bool(settings["api_key"] and settings["base_url"])


def chat_ready(cfg: dict[str, Any] | None = None) -> bool:
    return minimax_ready(cfg) or llm_ready(cfg)


def _voice_info(data: dict[str, Any]) -> dict[str, str]:
    if minimax_ready(data):
        return {"voice_mode": "minimax", "voice_mode_label": "MiniMax"}
    if (data.get("siliconflow_api_key") or "").strip():
        return {"voice_mode": "cosyvoice", "voice_mode_label": "CosyVoice"}
    return {"voice_mode": "browser", "voice_mode_label": "浏览器语音"}


def _cap_detail(data: dict[str, Any], subject: str) -> str:
    if data.get("llm_related_can_win"):
        return f"{subject}已允许模型分直接达到猜中阈值。超时或失败则只用本地拼音+字面。谐音如果本地分已经够高，仍按本地规则判。"
    return f"{subject}模型分封顶在猜中阈值之下，同义词不会单靠模型分获胜。超时或失败则只用本地拼音+字面。谐音如果本地分已经够高，仍按本地规则判。"


def scoring_info(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    data = cfg or load_config()
    has_key = bool((data.get("siliconflow_api_key") or "").strip())
    has_llm = llm_ready(data)
    has_minimax = minimax_ready(data)
    voice = _voice_info(data)
    if has_minimax:
        return {
            "has_api_key": has_key,
            "has_llm": has_llm,
            "has_minimax": True,
            "scoring_mode": "minimax_embed",
            "scoring_mode_label": "MiniMax 向量",
            "scoring_mode_detail": _cap_detail(data, "后台批量缓存 embo 向量，弹幕即时计分。"),
            **voice,
        }
    if has_key:
        return {
            "has_api_key": True,
            "has_llm": has_llm,
            "has_minimax": False,
            "scoring_mode": "siliconflow_embed",
            "scoring_mode_label": "硅基流动向量",
            "scoring_mode_detail": "向量相似度与本地拼音/字面取较高值，谐音不会丢",
            **voice,
        }
    if has_llm:
        return {
            "has_api_key": False,
            "has_llm": True,
            "has_minimax": False,
            "scoring_mode": "llm_related",
            "scoring_mode_label": "大模型相关词",
            "scoring_mode_detail": _cap_detail(data, "后台预取相关词并缓存，弹幕即时计分。"),
            **voice,
        }
    return {
        "has_api_key": False,
        "has_llm": False,
        "has_minimax": False,
        "scoring_mode": "local_pinyin",
        "scoring_mode_label": "本地拼音+字面",
        "scoring_mode_detail": "未配置 API Key，谐音、相关词表与字形离线计分",
        **voice,
    }


def danmaku_settings(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (cfg or load_config()).get("danmaku") or {}

    def num(key: str, default: float, lo: float, hi: float, cast=float):
        try:
            value = cast(raw.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    enabled = raw.get("enabled", True)
    return {
        "enabled": bool(enabled) and enabled not in {0, "0", "false", "False"},
        "speed": num("speed", 8, 4, 20),
        "font_size": int(num("font_size", 32, 20, 72, int)),
        "opacity": num("opacity", 0.82, 0.25, 1),
        "lanes": int(num("lanes", 4, 2, 12, int)),
        "per_second": int(num("per_second", 6, 1, 20, int)),
        "band_top": int(num("band_top", 18, 0, 70, int)),
        "band_height": int(num("band_height", 18, 10, 50, int)),
    }


def public_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    data = deepcopy(cfg or load_config())
    key = data.get("siliconflow_api_key") or ""
    llm_key = data.get("llm_api_key") or ""
    mm_key = data.get("minimax_api_key") or ""
    data["has_api_key"] = bool(key)
    data["siliconflow_api_key_masked"] = _mask_key(key)
    data["llm_api_key_masked"] = _mask_key(llm_key)
    data["minimax_api_key_masked"] = _mask_key(mm_key)
    data.pop("siliconflow_api_key", None)
    data.pop("llm_api_key", None)
    data.pop("minimax_api_key", None)
    data["chat_models"] = CHAT_MODELS
    data["minimax_voices"] = MINIMAX_VOICES
    data.update(scoring_info(cfg or load_config()))
    return data


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def api_key() -> str:
    return (load_config().get("siliconflow_api_key") or "").strip()
