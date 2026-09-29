from __future__ import annotations

import json
import random
import time
from typing import Any

from app.bus import ChatEvent
from app.config import load_config
from app.games.base import BaseGame
from app.scoring import grant_win, note_miss, win_suffix
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
        self.eliminated: set[int] = set()
        self.clue = ""

    def _params(self) -> dict:
        return load_config().get("quiz", {})

    def _arm(self) -> None:
        self.attempts = []
        self.winner = ""
        self.eliminated = set()
        self.clue = ""
        self.status = "playing"
        self.started_at = time.time()
        self.ends_at = self.started_at + int(self._params().get("countdown") or 60)

    def _choose_question(self, specified: str = "", avoid: str = "") -> dict:
        bank = load_questions()
        chosen = None
        if specified:
            for item in bank:
                if specified in (item.get("question") or "") or specified == (item.get("answer") or ""):
                    chosen = item
                    break
        if chosen is None:
            pool = [
                q
                for q in bank
                if (q.get("question") or "") not in self.used and (q.get("question") or "") != avoid
            ]
            if not pool:
                pool = [q for q in bank if (q.get("question") or "") != avoid]
            chosen = random.choice(pool or bank or [_fallback_question()])
        self.question = dict(chosen)
        self.used.add(self.question.get("question") or "")
        if len(self.used) >= max(1, len(bank)):
            self.used.clear()
            self.used.add(self.question.get("question") or "")
        return self.question

    def start_round(self, specified: str = "") -> list[str]:
        self.round_no += 1
        self._choose_question(specified)
        self._arm()
        self.announcement = f"第{self.round_no}题：请在弹幕发送 A/B/C/D 或答案"
        return ["start", "announce"]

    def unlock_hint(self, nickname: str = "") -> str:
        if self.status != "playing":
            return ""
        options = self.question.get("options") or []
        letters = ["A", "B", "C", "D"]
        wrong = [i for i, opt in enumerate(options) if i not in self.eliminated and not self._option_is_answer(i, opt)]
        if wrong:
            index = wrong[0]
            self.eliminated.add(index)
            label = letters[index] if index < len(letters) else str(index + 1)
            return f"排除 {label}"
        answer = str(self.question.get("answer") or "")
        if answer and not self.clue:
            self.clue = f"答案开头是 {answer[0]}"
            return self.clue
        return ""

    def _option_is_answer(self, index: int, opt: object) -> bool:
        letters = ["A", "B", "C", "D"]
        answer = normalize_answer(str(self.question.get("answer") or ""))
        if normalize_answer(str(opt)) == answer:
            return True
        return index < len(letters) and answer == letters[index]

    def refresh_prompt(self) -> str:
        if self.status != "playing":
            return ""
        previous = self.question.get("question") or ""
        self.round_no += 1
        self._choose_question("", avoid=previous)
        self._arm()
        return "已刷新本题"

    def on_timeout(self) -> list[str]:
        ans = self.question.get("answer") or ""
        self.begin_reveal(f"时间到，正确答案是 {ans}")
        return ["timeout", "announce"]

    def skip(self) -> list[str]:
        ans = self.question.get("answer") or ""
        self.status = "idle"
        self.announcement = f"已跳过，答案是 {ans}" if ans else "已跳过"
        return ["skip", "announce"]

    def on_comment(self, event: ChatEvent) -> list[str]:
        if self.status != "playing":
            return []
        self.last_award = None
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
            award = grant_win(
                event.user_id,
                event.nickname,
                int(self._params().get("win_points") or 80) + leftover,
                reason="quiz",
            )
            self.last_award = award
            self.winner = event.nickname
            self.last_winner = event.nickname
            self.begin_reveal(f"恭喜 {event.nickname} 抢答正确{win_suffix(award)}")
            notes.extend(["win", "announce"])
        elif not correct and _looks_like_attempt(text, self.question):
            note_miss(event.user_id, event.nickname)
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
            "eliminated": sorted(self.eliminated),
            "clue": self.clue,
            "announcement": self.announcement,
        }

    def host_state(self) -> dict[str, Any]:
        ans = self.question.get("answer") or "—"
        return {
            "secret": ans,
            "status_text": f"当前回合正在 {self.status} - 答案是 [ {ans} ]",
        }


def _looks_like_attempt(text: str, question: dict) -> bool:
    guess = normalize_answer(text)
    if guess in {"A", "B", "C", "D"}:
        return True
    options = question.get("options") or []
    return guess in {normalize_answer(str(opt)) for opt in options}


def _fallback_question() -> dict:
    return {
        "question": "一年有多少个月？",
        "options": ["10", "11", "12", "13"],
        "answer": "12",
        "aliases": ["十二", "十二个月"],
    }
