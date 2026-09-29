from __future__ import annotations

import random
import time
from typing import Any

from pypinyin import Style, lazy_pinyin

from app.bus import ChatEvent
from app.config import load_config
from app.games.base import BaseGame
from app.games.emoji_bank import all_puzzles
from app.games.similarity import normalize_word
from app.scoring import grant_win, note_miss, win_suffix

_PREFIXES = ("我觉得是", "我猜是", "答案是", "应该是", "猜是", "猜")


def loose_text(text: str) -> str:
    word = normalize_word(text)
    for prefix in _PREFIXES:
        if word.startswith(prefix) and len(word) > len(prefix):
            word = word[len(prefix):]
            break
    return word


def same_pinyin(guess: str, answer: str) -> bool:
    if not guess or not answer or len(guess) != len(answer):
        return False
    return lazy_pinyin(guess, style=Style.NORMAL) == lazy_pinyin(answer, style=Style.NORMAL)


class EmojiGame(BaseGame):
    game_id = "emoji"
    title = "看图猜"

    def __init__(self) -> None:
        super().__init__()
        self.puzzle: dict = {}
        self.hints: list[str] = []
        self.attempts: list[dict] = []
        self.winner = ""
        self.used: set[str] = set()
        self.next_hint_at = 0.0
        self.last_category = ""

    def _params(self) -> dict:
        return load_config().get("emoji") or {}

    def start_round(self, specified: str = "") -> list[str]:
        params = self._params()
        self.round_no += 1
        self.puzzle = self._pick(specified, str(params.get("category") or "rotate"))
        self.hints = []
        self.attempts = []
        self.winner = ""
        self.status = "playing"
        self.started_at = time.time()
        countdown = int(params.get("countdown") or 70)
        self.ends_at = self.started_at + max(15, countdown)
        self.next_hint_at = self.started_at + int(params.get("hint_interval") or 20)
        label = self.category_label()
        self.announcement = f"看图猜{label}，发弹幕答题"
        self.last_winner = ""
        return ["start", "announce"]

    def _pick(self, specified: str, category: str) -> dict:
        bank = all_puzzles()
        want = category
        if want == "rotate":
            want = "song" if self.last_category == "idiom" else "idiom"
        pool = [item for item in bank if item.get("category") == want] or bank
        target = normalize_word(specified)
        if target:
            for item in bank:
                if normalize_word(item.get("answer") or "") == target or target in (item.get("emojis") or ""):
                    self.last_category = str(item.get("category") or want)
                    self.used.add(str(item.get("id")))
                    return item
        fresh = [item for item in pool if str(item.get("id")) not in self.used]
        choice = random.choice(fresh or pool or [{"id": "fallback", "category": "idiom", "emojis": "🐴🐯", "answer": "马马虎虎", "aliases": [], "hint": "四个字"}])
        self.last_category = str(choice.get("category") or "idiom")
        self.used.add(str(choice.get("id")))
        return choice

    def category_label(self) -> str:
        return "成语" if (self.puzzle.get("category") or self.last_category) != "song" else "歌名"

    def on_timeout(self) -> list[str]:
        self.begin_reveal(f"时间到，答案是 {self.puzzle.get('answer') or ''}")
        return ["timeout", "announce"]

    def skip(self) -> list[str]:
        answer = str(self.puzzle.get("answer") or "")
        self.status = "idle"
        self.announcement = f"已跳过，答案是 {answer}" if answer else "已跳过"
        return ["skip", "announce"]

    def refresh_prompt(self) -> str:
        if self.status not in {"playing", "reveal", "idle"}:
            return ""
        self.start_round("")
        return "已换一道新题"

    def maybe_hint(self, now: float | None = None) -> list[str]:
        now = now if now is not None else time.time()
        params = self._params()
        if self.status != "playing":
            return []
        limit = int(params.get("hints_per_round") or 3)
        if len(self.hints) >= limit or now < self.next_hint_at:
            return []
        if self.unlock_hint(""):
            self.next_hint_at = now + int(params.get("hint_interval") or 20)
            return ["hint"]
        return []

    def unlock_hint(self, nickname: str = "") -> str:
        if self.status != "playing":
            return ""
        answer = str(self.puzzle.get("answer") or "")
        if not answer:
            return ""
        step = len(self.hints)
        if step <= 0:
            text = str(self.puzzle.get("hint") or "") or f"共 {len(answer)} 个字"
        elif step == 1:
            text = f"开头是 {answer[0]}"
        elif step == 2 and len(answer) > 2:
            text = f"还包含 {answer[len(answer)//2]}"
        else:
            return ""
        if text not in self.hints:
            self.hints.append(text)
        return text

    def _matches(self, guess: str) -> bool:
        answer = normalize_word(str(self.puzzle.get("answer") or ""))
        aliases = [normalize_word(str(item)) for item in (self.puzzle.get("aliases") or [])]
        if guess == answer or guess in aliases:
            return True
        if bool(self._params().get("allow_pinyin")) and same_pinyin(guess, answer):
            return True
        return False

    def on_comment(self, event: ChatEvent) -> list[str]:
        if self.status != "playing":
            return []
        self.last_award = None
        guess = loose_text(event.content)
        if len(guess) < 2 or len(guess) > 16:
            return []
        correct = self._matches(guess)
        self.attempts.append({"nickname": event.nickname, "text": guess, "correct": correct})
        self.attempts = self.attempts[-10:]
        if not correct:
            if len(guess) >= 2:
                note_miss(event.user_id, event.nickname)
            return ["guess"]
        params = self._params()
        award = grant_win(event.user_id, event.nickname, int(params.get("win_points") or 80), reason="emoji")
        self.last_award = award
        self.winner = event.nickname
        self.last_winner = event.nickname
        self.begin_reveal(f"恭喜 {event.nickname} 猜中了 {self.puzzle.get('answer')}{win_suffix(award)}")
        return ["guess", "win", "announce"]

    def public_state(self) -> dict[str, Any]:
        reveal = self.status == "reveal"
        return {
            "title": self.title,
            "round": self.round_no,
            "status": self.status,
            "countdown": self.remaining(),
            "emojis": self.puzzle.get("emojis") or "",
            "category": self.puzzle.get("category") or "",
            "category_label": self.category_label() if self.puzzle else "",
            "hints": list(self.hints),
            "attempts": list(self.attempts),
            "winner": self.winner,
            "reveal": self.puzzle.get("answer") if reveal else "",
            "announcement": self.announcement,
        }

    def host_state(self) -> dict[str, Any]:
        answer = self.puzzle.get("answer") or "—"
        return {
            "status_text": f"看图猜{self.category_label()} - 答案是 [ {answer} ]",
            "answer": self.puzzle.get("answer") or "",
        }
