from __future__ import annotations

import json
import random
import time
from typing import Any

from app.bus import ChatEvent
from app.config import load_config
from app.db import add_points
from app.games.base import BaseGame
from app.games.similarity import normalize_word
from app.paths import QUESTIONS_PATH


def load_questions(path=None) -> list[dict]:
    target = path or QUESTIONS_PATH
    if not target.exists():
        return []
    data = json.loads(target.read_text(encoding="utf-8"))
    return list(data) if isinstance(data, list) else []


def save_questions(items: list[dict], overwrite: bool = False, path=None) -> list[dict]:
    target = path or QUESTIONS_PATH
    current = [] if overwrite else load_questions(target)
    seen = {(q.get("question") or "") for q in current}
    for item in items:
        q = (item.get("question") or "").strip()
        if q and q not in seen:
            current.append(item)
            seen.add(q)
    target.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def normalize_answer(text: str) -> str:
    return normalize_word(text).upper()


def is_correct_answer(text: str, question: dict) -> bool:
    guess = normalize_answer(text)
    if not guess or not question:
        return False
    answer = normalize_answer(str(question.get("answer") or ""))
    options = question.get("options") or []
    letters = ["A", "B", "C", "D"]
    if guess in letters:
        idx = letters.index(guess)
        if idx < len(options) and normalize_answer(str(options[idx])) == answer:
            return True
        if guess == answer:
            return True
        # 允许 A/B/C/D 直接对应标准答案字母
        if answer in letters and guess == answer:
            return True
    if guess == answer:
        return True
    for i, opt in enumerate(options):
        if normalize_answer(str(opt)) == guess and normalize_answer(str(opt)) == answer:
            return True
        if normalize_answer(str(opt)) == guess and answer == letters[i]:
            return True
    aliases = question.get("aliases") or []
    return guess in {normalize_answer(str(a)) for a in aliases}


class QuizGame(BaseGame):
    game_id = "quiz"
    title = "弹幕答题"

    def __init__(self) -> None:
        super().__init__()
        self.question: dict = {}
        self.attempts: list[dict] = []
        self.winner = ""
        self.used: set[str] = set()

    def _params(self) -> dict:
        return load_config().get("quiz", {})

    def start_round(self, specified: str = "") -> list[str]:
        bank = load_questions()
        chosen = None
        if specified:
            for item in bank:
                if specified in (item.get("question") or "") or specified == (item.get("answer") or ""):
                    chosen = item
                    break
        pool = [q for q in bank if (q.get("question") or "") not in self.used]
        if not chosen:
            chosen = random.choice(pool or bank or [_fallback_question()])
        self.round_no += 1
        self.question = dict(chosen)
        self.used.add(self.question.get("question") or "")
        if len(self.used) >= max(1, len(bank)):
            self.used.clear()
        self.attempts = []
        self.winner = ""
        self.status = "playing"
        self.started_at = time.time()
        self.ends_at = self.started_at + int(self._params().get("countdown") or 60)
        self.announcement = f"第{self.round_no}题：请在弹幕发送 A/B/C/D 或答案"
        return ["start", "announce"]

    def on_timeout(self) -> list[str]:
        self.status = "reveal"
        self.reveal_until = time.time() + int(self._params().get("post_round_delay") or 6)
        ans = self.question.get("answer") or ""
        self.announcement = f"时间到，正确答案是 {ans}"
        return ["timeout", "announce"]

    def skip(self) -> list[str]:
        ans = self.question.get("answer") or ""
        self.status = "idle"
        self.announcement = f"已跳过，答案是 {ans}" if ans else "已跳过"
        return ["skip", "announce"]

    def on_comment(self, event: ChatEvent) -> list[str]:
        if self.status != "playing":
            return []
        text = (event.content or "").strip()
        if not text or len(text) > 20:
            return []
        correct = is_correct_answer(text, self.question)
        self.attempts.append(
            {
                "nickname": event.nickname,
                "user_id": event.user_id,
                "text": text,
                "correct": correct,
                "ts": event.ts,
            }
        )
        notes = ["guess"]
        if correct and not self.winner:
            leftover = self.remaining()
            add_points(event.user_id, event.nickname, int(self._params().get("win_points") or 80) + leftover)
            self.winner = event.nickname
            self.last_winner = event.nickname
            self.status = "reveal"
            self.reveal_until = time.time() + int(self._params().get("post_round_delay") or 6)
            self.announcement = f"恭喜 {event.nickname} 抢答正确"
            notes.extend(["win", "announce"])
        return notes

    def public_state(self) -> dict[str, Any]:
        q = self.question or {}
        reveal = self.status == "reveal"
        return {
            "title": self.title,
            "round": self.round_no,
            "status": self.status,
            "countdown": self.remaining(),
            "question": q.get("question") or "",
            "options": q.get("options") or [],
            "attempts": self.attempts[-12:],
            "winner": self.winner,
            "reveal": q.get("answer") if reveal else "",
            "announcement": self.announcement,
        }

    def host_state(self) -> dict[str, Any]:
        ans = self.question.get("answer") or "—"
        return {
            "secret": ans,
            "status_text": f"当前回合正在 {self.status} - 答案是 [ {ans} ]",
        }


def _fallback_question() -> dict:
    return {
        "question": "一年有多少个月？",
        "options": ["10", "11", "12", "13"],
        "answer": "12",
        "aliases": ["十二", "十二个月"],
    }
