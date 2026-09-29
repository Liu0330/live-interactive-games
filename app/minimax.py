from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import minimax_ready, minimax_settings

CHAT_TIMEOUT = 20.0
EMBED_TIMEOUT = 8.0
TTS_TIMEOUT = 15.0

_THINK = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


class MinimaxError(RuntimeError):
    pass


def strip_think(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def _settings() -> dict:
    settings = minimax_settings()
    if not settings["api_key"]:
        raise MinimaxError("未配置 MiniMax API Key")
    return settings


def complete_chat(
    messages: list[dict[str, str]],
    *,
    timeout: float = CHAT_TIMEOUT,
    temperature: float = 0.2,
    max_tokens: int = 1800,
) -> str:
    """Anthropic 兼容接口。回复若夹了 think 标签也会剥掉。"""
    settings = _settings()
    system_parts: list[str] = []
    dialog: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        dialog.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    if not dialog:
        dialog = [{"role": "user", "content": "请只回复：pong"}]
    payload: dict[str, Any] = {
        "model": settings["chat_model"],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": dialog,
    }
    if system_parts:
        payload["system"] = "\n".join(system_parts)
    url = f"{settings['base_url']}/anthropic/v1/messages"
    headers = {
        "x-api-key": settings["api_key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise MinimaxError("MiniMax 对话超时") from exc
    except httpx.HTTPError as exc:
        raise MinimaxError("MiniMax 对话不可用") from exc
    if resp.status_code >= 400:
        raise MinimaxError(f"MiniMax 对话失败 HTTP {resp.status_code}: {resp.text[:240]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise MinimaxError("MiniMax 对话没有返回 JSON") from exc
    blocks = data.get("content") or []
    text = ""
    if blocks and isinstance(blocks[0], dict):
        text = str(blocks[0].get("text") or "")
    text = strip_think(text)
    if not text:
        raise MinimaxError("MiniMax 对话没有正文")
    return text


def test_connection() -> dict[str, Any]:
    if not minimax_ready():
        raise MinimaxError("请先保存 MiniMax 密钥")
    model = minimax_settings()["chat_model"]
    reply = complete_chat(
        [{"role": "user", "content": "请只回复：pong"}],
        timeout=CHAT_TIMEOUT,
        temperature=0,
        max_tokens=64,
    )
    return {"ok": True, "model": model, "reply": reply}


def embed_texts(texts: list[str], kind: str) -> list[list[float]] | None:
    """谜底用 type=db，观众猜测用 type=query。失败返回 None，调用方继续用本地分。"""
    clean = [str(item).strip() for item in texts if str(item).strip()]
    if not clean or not minimax_ready():
        return None
    settings = minimax_settings()
    payload = {
        "model": settings["embed_model"],
        "texts": clean,
        "type": "query" if kind == "query" else "db",
    }
    url = f"{settings['base_url']}/v1/embeddings"
    headers = {"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=EMBED_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=payload)
    except (httpx.TimeoutException, httpx.HTTPError):
        return None
    if resp.status_code >= 400:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    vectors = data.get("vectors")
    if not isinstance(vectors, list) or len(vectors) != len(clean):
        return None
    out: list[list[float]] = []
    for row in vectors:
        if not isinstance(row, list) or not row:
            return None
        try:
            out.append([float(value) for value in row])
        except (TypeError, ValueError):
            return None
    return out


_EMOTION_MODELS = {
    "speech-02-hd",
    "speech-02-turbo",
    "speech-01-hd",
    "speech-01-turbo",
    "speech-2.6-hd",
    "speech-2.6-turbo",
    "speech-2.8-hd",
    "speech-2.8-turbo",
}


def synthesize_speech(text: str) -> bytes:
    settings = _settings()
    spoken = (text or "").strip()
    if not spoken:
        raise MinimaxError("没有可播报的文字")
    model = str(settings["tts_model"])
    voice = {
        "voice_id": settings["tts_voice"],
        "speed": settings.get("tts_speed", 0.92),
        "vol": 1,
        "pitch": 0,
    }
    emotion = str(settings.get("tts_emotion") or "")
    if emotion and model in _EMOTION_MODELS:
        voice["emotion"] = emotion
    payload = {
        "model": model,
        "text": spoken[:500],
        "stream": False,
        "voice_setting": voice,
        "audio_setting": {"format": "mp3", "sample_rate": 32000},
    }
    url = f"{settings['base_url']}/v1/t2a_v2"
    headers = {"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=TTS_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise MinimaxError("MiniMax 语音超时") from exc
    except httpx.HTTPError as exc:
        raise MinimaxError("MiniMax 语音不可用") from exc
    if resp.status_code >= 400:
        raise MinimaxError(f"MiniMax 语音失败 HTTP {resp.status_code}: {resp.text[:240]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise MinimaxError("MiniMax 语音没有返回 JSON") from exc
    status = (data.get("base_resp") or {}).get("status_code", 0)
    try:
        status_code = int(status)
    except (TypeError, ValueError):
        status_code = -1
    if status_code != 0:
        message = str((data.get("base_resp") or {}).get("status_msg") or "语音失败")
        raise MinimaxError(message)
    audio_hex = str(((data.get("data") or {}).get("audio")) or "")
    audio_hex = re.sub(r"\s+", "", audio_hex)
    if not audio_hex or len(audio_hex) % 2:
        raise MinimaxError("MiniMax 没有返回音频")
    try:
        raw = bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise MinimaxError("MiniMax 音频无法解码") from exc
    if not raw:
        raise MinimaxError("MiniMax 没有返回音频")
    return raw
