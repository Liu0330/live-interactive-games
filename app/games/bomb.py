from __future__ import annotations

import random
import re
import time
from typing import Any

from app.bus import ChatEvent
from app.config import load_config
from app.db import add_points
from app.games.base import BaseGame

_INT = re.compile(r"^-?\d+$")


def parse_guess(text: str) -> int | None:
    raw = (text or "").strip()
    if not _INT.match(raw):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def apply_guess(low: int, high: int, secret: int, guess: int) -> dict:
    """更新区间。返回 hint / exploded / invalid。"""
    if guess < low or guess > high:
        return {
            "ok": False,
            "low": low,
            "high": high,
            "hint": "out",
            "exploded": False,
        }
    if guess == secret:
        return {"ok": True, "low": low, "high": high, "hint": "hit", "exploded": True}
    if guess < secret:
        return {"ok": True, "low": guess + 1, "high": high, "hint": "low", "exploded": False}
    return {"ok": True, "low": low, "high": guess - 1, "hint": "high", "exploded": False}


class BombGame(BaseGame):
    game_id = "bomb"
    title = "数字炸弹"

    def __init__(self) -> None:
        super().__init__()
        self.secret = 0
        self.low = 1
        self.high = 100
        self.guesses: list[dict] = []
        self.winner = ""
        self.last_safe = ""
        self.last_safe_user = ""
        self.mode = "hit"

    def _params(self) -> dict:
        return load_config().get("bomb", {})

    def start_round(self, specified: str = "") -> list[str]:
        params = self._params()
        self.low = int(params.get("min_value") or 1)
        self.high = int(params.get("max_value") or 100)
        if self.high <= self.low:
            self.high = self.low + 1
        if specified.strip().isdigit():
            value = int(specified.strip())
            if self.low <= value <= self.high:
                self.secret = value
            else:
                self.secret = random.randint(self.low, self.high)
        else:
            self.secret = random.randint(self.low, self.high)
        self.mode = str(params.get("mode") or "hit")
        self.round_no += 1
        self.guesses = []
        self.winner = ""
        self.last_safe = ""
        self.last_safe_user = ""
        self.status = "playing"
        self.started_at = time.time()
        self.ends_at = self.started_at + int(params.get("countdown") or 180)
        self.announcement = f"第{self.round_no}局，数字在 {self.low} 到 {self.high} 之间"
        return ["start", "announce"]

    def on_timeout(self) -> list[str]:
        self.status = "reveal"
        self.reveal_until = time.time() + int(self._params().get("post_round_delay") or 6)
        if self.mode == "last_safe" and self.last_safe:
            self.winner = self.last_safe
            add_points(self.last_safe_user, self.last_safe, int(self._params().get("win_points") or 80))
            self.announcement = f"时间到，最后安全猜测是 {self.last_safe}"
        else:
            self.announcement = f"时间到，炸弹是 {self.secret}"
        return ["timeout", "announce"]

    def skip(self) -> list[str]:
        self.status = "idle"
        self.announcement = f"已跳过，炸弹是 {self.secret}" if self.secret else "已跳过"
        return ["skip", "announce"]

    def on_comment(self, event: ChatEvent) -> list[str]:
        if self.status != "playing":
            return []
        guess = parse_guess(event.content)
        if guess is None:
            return []
        result = apply_guess(self.low, self.high, self.secret, guess)
        row = {
            "nickname": event.nickname,
            "user_id": event.user_id,
            "guess": guess,
            "hint": result["hint"],
            "ok": result["ok"],
        }
        self.guesses.append(row)
        notes = ["guess"]
        if not result["ok"]:
            return notes
        if result["exploded"]:
            if self.mode == "last_safe" and self.last_safe:
                self.winner = self.last_safe
                add_points(self.last_safe_user, self.last_safe, int(self._params().get("win_points") or 80))
                self.announcement = f"{event.nickname} 踩中炸弹 {self.secret}，{self.last_safe} 获胜"
            else:
                self.winner = event.nickname
                leftover = self.remaining()
                add_points(
                    event.user_id,
                    event.nickname,
                    int(self._params().get("win_points") or 80) + leftover,
                )
                self.announcement = f"{event.nickname} 踩中数字炸弹 {self.secret}"
            self.last_winner = self.winner
            self.status = "reveal"
            self.reveal_until = time.time() + int(self._params().get("post_round_delay") or 6)
            notes.extend(["win", "announce"])
            return notes
        self.low = result["low"]
        self.high = result["high"]
        self.last_safe = event.nickname
        self.last_safe_user = event.user_id
        if result["hint"] == "low":
            self.announcement = f"{guess} 太小了，范围 {self.low}-{self.high}"
        else:
            self.announcement = f"{guess} 太大了，范围 {self.low}-{self.high}"
        return notes

    def public_state(self) -> dict[str, Any]:
        reveal = self.status == "reveal"
        return {
            "title": self.title,
            "round": self.round_no,
            "status": self.status,
            "countdown": self.remaining(),
            "low": self.low,
            "high": self.high,
            "guesses": self.guesses[-16:],
            "winner": self.winner,
            "reveal": self.secret if reveal else "",
            "mode": self.mode,
            "announcement": self.announcement,
        }

    def host_state(self) -> dict[str, Any]:
        return {
            "secret": self.secret,
            "status_text": f"当前回合正在 {self.status} - 炸弹是 [ {self.secret} ]，范围 {self.low}-{self.high}",
        }
