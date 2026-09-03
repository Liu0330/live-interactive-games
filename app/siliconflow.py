from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import api_key, load_config

BASE_URL = "https://api.siliconflow.cn/v1"


class SiliconFlowError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    key = api_key()
    if not key:
        raise SiliconFlowError("未配置硅基流动 API Key")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def test_connection() -> dict[str, Any]:
    cfg = load_config()
    model = cfg.get("chat_model") or "deepseek-ai/DeepSeek-V3"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "请只回复：pong"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{BASE_URL}/chat/completions", headers=_headers(), json=payload)
        if resp.status_code >= 400:
            raise SiliconFlowError(f"连接失败 HTTP {resp.status_code}: {resp.text[:240]}")
        data = resp.json()
    text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    return {"ok": True, "model": model, "reply": text}


def chat_json(prompt: str, system: str) -> Any:
    cfg = load_config()
    model = cfg.get("chat_model") or "deepseek-ai/DeepSeek-V3"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
    }
    with httpx.Client(timeout=90) as client:
        resp = client.post(f"{BASE_URL}/chat/completions", headers=_headers(), json=payload)
        if resp.status_code >= 400:
            raise SiliconFlowError(f"生成失败 HTTP {resp.status_code}: {resp.text[:240]}")
        data = resp.json()
    text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    return _extract_json(text)


def generate_words(n: int, theme: str = "") -> list[str]:
    theme = theme.strip() or "日常生活常见事物"
    system = (
        "你是直播互动游戏的词库助手。只输出 JSON 数组，元素是中文词语字符串。"
        "词语必须健康、常见、适合全年龄，禁止政治、色情、暴力、歧视、违禁品。"
    )
    prompt = (
        f"请生成 {max(1, min(n, 80))} 个适合「语义猜词」的中文词语，主题：{theme}。"
        "2-4 个字为主，不要重复，不要解释。"
    )
    data = chat_json(prompt, system)
    words: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                words.append(item)
            elif isinstance(item, dict):
                words.append(str(item.get("word") or item.get("词语") or ""))
    return [w.strip() for w in words if w and str(w).strip()]


def generate_questions(n: int, theme: str = "") -> list[dict]:
    theme = theme.strip() or "生活常识"
    system = (
        "你是中文知识问答出题助手。只输出 JSON 数组，每项含 question, options(4个), answer。"
        "题目健康、适合直播，禁止政治敏感与成人内容。"
    )
    prompt = f"生成 {max(1, min(n, 20))} 道{theme}选择题。answer 必须是 options 中的一项原文。"
    data = chat_json(prompt, system)
    out: list[dict] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "question": str(item.get("question") or "").strip(),
                    "options": [str(x) for x in (item.get("options") or [])][:4],
                    "answer": str(item.get("answer") or "").strip(),
                    "aliases": item.get("aliases") or [],
                }
            )
    return [q for q in out if q["question"] and q["answer"]]


def embed_texts(texts: list[str]) -> list[list[float]]:
    cfg = load_config()
    model = cfg.get("embed_model") or "BAAI/bge-m3"
    payload = {"model": model, "input": texts, "encoding_format": "float"}
    with httpx.Client(timeout=45) as client:
        resp = client.post(f"{BASE_URL}/embeddings", headers=_headers(), json=payload)
        if resp.status_code >= 400:
            raise SiliconFlowError(f"向量失败 HTTP {resp.status_code}: {resp.text[:240]}")
        data = resp.json()
    rows = sorted(data.get("data") or [], key=lambda x: int(x.get("index") or 0))
    return [list(row.get("embedding") or []) for row in rows]


def embed_similarity(guess: str, secret: str) -> float:
    from app.games.similarity import embedding_percent, local_similarity

    try:
        vecs = embed_texts([guess, secret])
        if len(vecs) == 2 and vecs[0] and vecs[1]:
            return embedding_percent(vecs[0], vecs[1])
    except Exception:
        pass
    return local_similarity(guess, secret)


def synthesize_speech(text: str) -> bytes:
    cfg = load_config()
    payload = {
        "model": cfg.get("tts_model") or "FunAudioLLM/CosyVoice2-0.5B",
        "voice": cfg.get("tts_voice") or "FunAudioLLM/CosyVoice2-0.5B:bella",
        "input": f"用轻松的直播口播语气说。<|endofprompt|>{text}",
        "response_format": "mp3",
        "speed": 1.05,
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(f"{BASE_URL}/audio/speech", headers=_headers(), json=payload)
        if resp.status_code >= 400:
            raise SiliconFlowError(f"语音失败 HTTP {resp.status_code}: {resp.text[:240]}")
        return resp.content


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise SiliconFlowError("模型没有返回可用 JSON")
