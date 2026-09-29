from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import llm_ready, llm_settings, load_config
from app.games.similarity import normalize_word

DEFAULT_TIMEOUT = 28.0


class ChatError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise ChatError("模型没有返回可用 JSON")


def chat_completion(
    messages: list[dict[str, str]],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    temperature: float = 0.2,
    max_tokens: int = 1800,
) -> str:
    base, key, model = llm_settings()
    if not base or not key:
        raise ChatError("未配置对话接口")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise ChatError("对话接口超时") from exc
    except httpx.HTTPError as exc:
        raise ChatError("对话接口不可用") from exc
    if resp.status_code >= 400:
        raise ChatError(f"对话失败 HTTP {resp.status_code}: {resp.text[:240]}")
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise ChatError("对话接口没有返回 JSON") from exc
    return (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()


def chat_json(prompt: str, system: str, *, timeout: float = DEFAULT_TIMEOUT, temperature: float = 0.4) -> Any:
    text = chat_completion(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        timeout=timeout,
        temperature=temperature,
        max_tokens=2048,
    )
    return _extract_json(text)


def test_connection() -> dict[str, Any]:
    if not llm_ready():
        raise ChatError("请先保存对话接口地址和密钥")
    _base, _key, model = llm_settings()
    reply = chat_completion(
        [{"role": "user", "content": "请只回复：pong"}],
        timeout=40,
        temperature=0,
        max_tokens=16,
    )
    return {"ok": True, "model": model, "reply": reply}


def _related_count() -> int:
    try:
        count = int(load_config().get("llm_related_count") or 48)
    except (TypeError, ValueError):
        count = 48
    return max(12, min(80, count))


def parse_related_payload(data: Any, secret: str) -> dict[str, float]:
    secret_n = normalize_word(secret)
    rows: list[Any]
    if isinstance(data, dict) and isinstance(data.get("words"), list):
        rows = data["words"]
    elif isinstance(data, dict):
        rows = [{"word": key, "score": value} for key, value in data.items() if key != "words"]
    elif isinstance(data, list):
        rows = data
    else:
        return {}
    out: dict[str, float] = {}
    for item in rows:
        if isinstance(item, str):
            word, score = item, 60
        elif isinstance(item, dict):
            word = str(item.get("word") or item.get("词语") or "")
            score = item.get("score", item.get("相关度"))
        else:
            continue
        word_n = normalize_word(word)
        if not word_n or word_n == secret_n or len(word_n) > 12:
            continue
        try:
            value = float(score)
        except (TypeError, ValueError):
            continue
        value = max(0.0, min(100.0, value))
        out[word_n] = max(out.get(word_n, 0.0), value)
        if len(out) >= 80:
            break
    return out


def fetch_related(secret: str) -> dict[str, float] | None:
    secret = normalize_word(secret)
    if not secret or not llm_ready():
        return None
    system = (
        "你是语义相关度标注器。只输出一个 JSON 对象，不要解释，不要 Markdown。"
        '格式严格为 {"words":[{"word":"词语","score":0}]}。'
        "score 是 0 到 100 的整数，表示和谜底的语义相关程度。"
        "不要输出谜底本身。禁止政治、色情、暴力、歧视内容。"
        "谐音但语义无关的词，分数不要超过 40。"
    )
    prompt = (
        f"谜底是「{secret}」。请给出 {_related_count()} 个观众可能会猜的相关中文词，"
        "覆盖同义词、近义词、上下位词和常见联想。只输出 JSON。"
    )
    try:
        data = chat_json(prompt, system, timeout=DEFAULT_TIMEOUT, temperature=0.2)
    except (ChatError, json.JSONDecodeError, TypeError, ValueError):
        return None
    mapping = parse_related_payload(data, secret)
    return mapping or None


def fetch_refine(secret: str, words: list[str]) -> dict[str, float] | None:
    secret_n = normalize_word(secret)
    clean = []
    for word in words:
        item = normalize_word(word)
        if item and item != secret_n and item not in clean:
            clean.append(item)
    if not secret_n or not clean or not llm_ready():
        return None
    system = (
        "你是语义相关度标注器。只输出 JSON 对象，不要解释。"
        '格式严格为 {"words":[{"word":"词语","score":0}]}。'
        "只给列出的猜测打分，分数 0 到 100。谐音但语义无关不要超过 40。"
    )
    listed = "、".join(clean[:12])
    prompt = f"谜底是「{secret_n}」。请为这些猜测打语义相关分：{listed}。只输出 JSON。"
    try:
        data = chat_json(prompt, system, timeout=DEFAULT_TIMEOUT, temperature=0.1)
    except (ChatError, json.JSONDecodeError, TypeError, ValueError):
        return None
    mapping = parse_related_payload(data, secret_n)
    allowed = set(clean)
    return {word: score for word, score in mapping.items() if word in allowed} or None


def generate_words(n: int, theme: str = "") -> list[str]:
    if llm_ready():
        return _generate_words_llm(n, theme)
    from app.siliconflow import generate_words as silicon_words

    return silicon_words(n, theme)


def generate_questions(n: int, theme: str = "") -> list[dict]:
    if llm_ready():
        return _generate_questions_llm(n, theme)
    from app.siliconflow import generate_questions as silicon_questions

    return silicon_questions(n, theme)


def _generate_words_llm(n: int, theme: str) -> list[str]:
    theme = theme.strip() or "日常生活常见事物"
    system = (
        "你是直播互动游戏的词库助手。只输出 JSON 数组，元素是中文词语字符串。"
        "词语必须健康、常见、适合全年龄，禁止政治、色情、暴力、歧视、违禁品。"
    )
    prompt = (
        f"请生成 {max(1, min(int(n), 80))} 个适合「语义猜词」的中文词语，主题：{theme}。"
        "2-4 个字为主，不要重复，不要解释。"
    )
    data = chat_json(prompt, system, temperature=0.7)
    words: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                words.append(item)
            elif isinstance(item, dict):
                words.append(str(item.get("word") or item.get("词语") or ""))
    return [w.strip() for w in words if w and str(w).strip()]


def _generate_questions_llm(n: int, theme: str) -> list[dict]:
    theme = theme.strip() or "生活常识"
    system = (
        "你是中文知识问答出题助手。只输出 JSON 数组，每项含 question, options(4个), answer。"
        "题目健康、适合直播，禁止政治敏感与成人内容。"
    )
    prompt = f"生成 {max(1, min(int(n), 20))} 道{theme}选择题。answer 必须是 options 中的一项原文。"
    data = chat_json(prompt, system, temperature=0.7)
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
