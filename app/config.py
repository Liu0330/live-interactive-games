from __future__ import annotations

import json
import os
from copy import deepcopy
from threading import Lock
from typing import Any

from app.paths import CONFIG_PATH, ensure_user_dirs

DEFAULT_CONFIG: dict[str, Any] = {
    "siliconflow_api_key": "",
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
    "gifts": [
        {"name": "小心心", "action": "random_words", "count": 50, "label": "随机 50 词"},
        {"name": "大啤酒", "action": "random_words", "count": 100, "label": "随机 100 词"},
        {"name": "鲜花", "action": "random_words", "count": 300, "label": "随机 300 词"},
        {"name": "你最好看", "action": "extra_hint", "count": 1, "label": "解锁提示"},
        {"name": "点赞", "action": "like", "count": 1, "threshold": 30, "label": "点赞满 30 次随机提示"},
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


def public_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    data = deepcopy(cfg or load_config())
    key = data.get("siliconflow_api_key") or ""
    data["has_api_key"] = bool(key)
    data["siliconflow_api_key_masked"] = _mask_key(key)
    data.pop("siliconflow_api_key", None)
    data["chat_models"] = CHAT_MODELS
    return data


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def api_key() -> str:
    return (load_config().get("siliconflow_api_key") or "").strip()
