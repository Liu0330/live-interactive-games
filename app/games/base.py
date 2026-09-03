from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from app.bus import ChatEvent


class BaseGame(ABC):
    game_id = "base"
    title = "互动游戏"

    def __init__(self) -> None:
        self.round_no = 0
        self.status = "idle"
        self.started_at = 0.0
        self.ends_at = 0.0
        self.reveal_until = 0.0
        self.announcement = ""
        self.last_winner = ""

    def remaining(self) -> int:
        if self.status != "playing":
            return 0
        return max(0, int(self.ends_at - time.time()))

    def tick(self, now: float | None = None) -> list[str]:
        now = now if now is not None else time.time()
        notes: list[str] = []
        if self.status == "playing" and now >= self.ends_at:
            notes.extend(self.on_timeout())
        if self.status == "reveal" and now >= self.reveal_until:
            self.status = "idle"
        return notes

    def on_timeout(self) -> list[str]:
        self.status = "idle"
        return []

    def skip(self) -> list[str]:
        self.status = "idle"
        self.announcement = "已跳过当前回合"
        return ["skip"]

    @abstractmethod
    def start_round(self, specified: str = "") -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def on_comment(self, event: ChatEvent) -> list[str]:
        raise NotImplementedError

    def on_gift(self, event: ChatEvent) -> list[str]:
        return []

    @abstractmethod
    def public_state(self) -> dict[str, Any]:
        raise NotImplementedError

    def host_state(self) -> dict[str, Any]:
        return {}
