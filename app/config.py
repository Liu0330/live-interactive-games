from __future__ import annotations

import json
import os
from copy import deepcopy
from threading import Lock
from typing import Any

from app.paths import CONFIG_PATH, ensure_user_dirs

DEFAULT_CONFIG: dict[str, Any] = {
    "siliconflow_api_key": "",
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


def scoring_info(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    data = cfg or load_config()
    has_key = bool((data.get("siliconflow_api_key") or "").strip())
    has_llm = llm_ready(data)
    if has_key:
        return {
            "has_api_key": True,
            "has_llm": has_llm,
            "scoring_mode": "siliconflow_embed",
            "scoring_mode_label": "硅基流动向量",
            "scoring_mode_detail": "向量相似度与本地拼音/字面取较高值，谐音不会丢",
        }
    if has_llm:
        if data.get("llm_related_can_win"):
            detail = "后台预取相关词并缓存，弹幕即时计分。已允许相关词直接达到猜中阈值；超时或失败则只用本地拼音+字面。"
        else:
            detail = "后台预取相关词并缓存，弹幕即时计分。相关词分数低于猜中阈值，同义词不会单靠模型分获胜；超时或失败则只用本地拼音+字面。"
        return {
            "has_api_key": False,
            "has_llm": True,
            "scoring_mode": "llm_related",
            "scoring_mode_label": "大模型相关词",
            "scoring_mode_detail": detail,
        }
    return {
        "has_api_key": False,
        "has_llm": False,
        "scoring_mode": "local_pinyin",
        "scoring_mode_label": "本地拼音+字面",
        "scoring_mode_detail": "未配置 API Key，谐音、相关词表与字形离线计分",
    }


def public_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    data = deepcopy(cfg or load_config())
    key = data.get("siliconflow_api_key") or ""
    llm_key = data.get("llm_api_key") or ""
    data["has_api_key"] = bool(key)
    data["siliconflow_api_key_masked"] = _mask_key(key)
    data["llm_api_key_masked"] = _mask_key(llm_key)
    data.pop("siliconflow_api_key", None)
    data.pop("llm_api_key", None)
    data["chat_models"] = CHAT_MODELS
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
