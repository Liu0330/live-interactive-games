from __future__ import annotations

import threading
import time
from typing import Any

from app import db
from app.bus import ChatEvent, bus
from app.config import load_config, save_config
from app.games.bomb import BombGame
from app.games.lottery import LotteryGame
from app.games.quiz import QuizGame
from app.games.semantic import SemanticGame
from app.ranks import rank_title

GAMES = {
    "semantic": SemanticGame,
    "quiz": QuizGame,
    "bomb": BombGame,
    "lottery": LotteryGame,
}

GAME_LABELS = {
    "semantic": "语义猜词",
    "quiz": "弹幕答题",
    "bomb": "数字炸弹",
    "lottery": "弹幕抽奖",
}


class GameManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.games = {key: cls() for key, cls in GAMES.items()}
        cfg = load_config()
        active = cfg.get("active_game") or "semantic"
        self.active_id = active if active in self.games else "semantic"
        self.last_announce = ""
        self.announce_seq = 0
        bus.subscribe(self._on_event)

    @property
    def game(self):
        return self.games[self.active_id]

    def switch(self, game_id: str) -> None:
        if game_id not in self.games:
            raise ValueError("未知玩法")
        with self._lock:
            self.active_id = game_id
            save_config({"active_game": game_id})

    def start_round(self, specified: str = "") -> list[str]:
        with self._lock:
            if self.active_id == "semantic":
                from app.config import api_key

                if api_key():
                    from app.siliconflow import embed_similarity

                    self.game.embed_fn = embed_similarity
                else:
                    self.game.embed_fn = None
            notes = self.game.start_round(specified)
            self._note_announce(notes)
            return notes

    def skip(self) -> list[str]:
        with self._lock:
            notes = self.game.skip()
            self._note_announce(notes)
            return notes

    def tick(self) -> list[str]:
        with self._lock:
            notes = self.game.tick()
            if hasattr(self.game, "maybe_hint"):
                notes.extend(self.game.maybe_hint())
            self._note_announce(notes)
            return notes

    def _on_event(self, event: ChatEvent) -> None:
        with self._lock:
            if event.event_type == "gift":
                notes = self.game.on_gift(event)
            else:
                notes = self.game.on_comment(event)
            self._note_announce(notes)

    def _note_announce(self, notes: list[str]) -> None:
        if "announce" in notes and self.game.announcement:
            self.last_announce = self.game.announcement
            self.announce_seq += 1

    def snapshot(self, host: bool = False) -> dict[str, Any]:
        cfg = load_config()
        with self._lock:
            game = self.game
            public = game.public_state()
            host_extra = game.host_state() if host else {}
        board = db.leaderboard(
            16,
            cfg.get("rank_names"),
            int(cfg.get("points_per_sublevel") or 180),
        )
        public.update(
            {
                "game": self.active_id,
                "game_label": GAME_LABELS.get(self.active_id, game.title),
                "leaderboard": board,
                "gift_rules": cfg.get("gifts") or [],
                "announce_seq": self.announce_seq,
                "tts_enabled": bool(cfg.get("tts_enabled", True)),
                "now": time.time(),
            }
        )
        if host:
            public["host"] = host_extra
            public["active_game"] = self.active_id
        return public

    def host_status_text(self) -> str:
        with self._lock:
            extra = self.game.host_state()
        return extra.get("status_text") or f"当前玩法 {GAME_LABELS.get(self.active_id)}"


manager = GameManager()


def preview_rank(points: int) -> str:
    cfg = load_config()
    return rank_title(points, cfg.get("rank_names"), int(cfg.get("points_per_sublevel") or 180))
