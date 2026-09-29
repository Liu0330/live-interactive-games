from __future__ import annotations

import threading
import time
from typing import Any

from app.bus import ChatEvent, bus
from app.config import load_config, save_config
from app.engagement import get_engagement
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
        self.engagement = get_engagement()
        from app.embed_cache import set_listener as set_embed_listener
        from app.related_cache import set_listener

        set_listener(self._on_related)
        set_embed_listener(self._on_embeds)
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
                from app.config import api_key, minimax_ready

                if minimax_ready():
                    self.game.embed_fn = None
                elif api_key():
                    from app.siliconflow import embed_similarity

                    self.game.embed_fn = embed_similarity
                else:
                    self.game.embed_fn = None
            notes = self.game.start_round(specified)
            if self.active_id == "semantic":
                self._arm_semantic_llm()
                self._arm_minimax_embed()
            notes.extend(self.engagement.apply_pending(self.game))
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

    def _arm_semantic_llm(self) -> None:
        from app.config import chat_ready
        from app.related_cache import get_related, schedule_prefetch

        if not chat_ready():
            return
        game = self.game
        cached = get_related(game.secret)
        if cached:
            game.absorb_related(cached)
        else:
            schedule_prefetch(game.secret)
        prepared = getattr(game, "prepared", "")
        if prepared:
            schedule_prefetch(prepared)

    def _arm_minimax_embed(self) -> None:
        from app.config import minimax_ready
        from app.embed_cache import schedule_secret

        if not minimax_ready():
            return
        schedule_secret(self.game.secret)
        prepared = getattr(self.game, "prepared", "")
        if prepared:
            schedule_secret(prepared)

    def _on_embeds(self, _texts: list[str]) -> None:
        with self._lock:
            game = self.games.get("semantic")
            if game is None or not hasattr(game, "rescore_embeddings"):
                return
            before = game.status
            game.rescore_embeddings()
            if game.status != before and game.announcement:
                self._note_announce(["announce", "win"])

    def _on_related(self, secret: str, mapping: dict[str, float]) -> None:
        from app.games.similarity import normalize_word

        with self._lock:
            game = self.games.get("semantic")
            if game is None or normalize_word(game.secret) != normalize_word(secret):
                return
            before = game.status
            game.absorb_related(mapping)
            if game.status != before and game.announcement:
                self._note_announce(["announce", "win"])

    def _on_event(self, event: ChatEvent) -> None:
        with self._lock:
            if event.event_type == "like" or _is_like_gift(event):
                count = event.like_count or event.gift_count or 1
                notes = self.engagement.on_likes(self.game, event.nickname, count)
            elif event.event_type == "member":
                notes = self.engagement.on_member(event)
                welcome = (self.engagement.welcome or {}).get("text") or ""
                if welcome and hasattr(self.game, "announcement"):
                    self.game.announcement = welcome
                    if "announce" not in notes:
                        notes = [*notes, "announce"]
            elif event.event_type == "gift":
                self.game.last_award = None
                notes = self.engagement.on_gift(self.game, event)
                award = getattr(self.game, "last_award", None)
                if award:
                    self.engagement.note_award(str(award.get("nickname") or event.nickname), award)
            else:
                self.game.last_award = None
                notes = self.game.on_comment(event)
                award = getattr(self.game, "last_award", None)
                if award:
                    self.engagement.note_award(str(award.get("nickname") or event.nickname), award)
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
        from app.config import scoring_info

        view = self.engagement.public_view()
        public.pop("likes", None)
        public.update(
            {
                "game": self.active_id,
                "game_label": GAME_LABELS.get(self.active_id, game.title),
                "leaderboard": view["boards"]["all"]["rows"],
                "gift_rules": view["gift_rules"],
                "announce_seq": self.announce_seq,
                "tts_enabled": bool(cfg.get("tts_enabled", True)),
                "now": time.time(),
                "like_bar": view["like_bar"],
                "bonus": view["bonus"],
                "effects": view["effects"],
                "effect_seq": view["effect_seq"],
                "welcome": view["welcome"],
                "combo": view["combo"],
                "boards": view["boards"],
                **scoring_info(cfg),
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


def _is_like_gift(event: ChatEvent) -> bool:
    return event.event_type == "gift" and (event.gift_name or "").strip() == "点赞"


def preview_rank(points: int) -> str:
    cfg = load_config()
    return rank_title(points, cfg.get("rank_names"), int(cfg.get("points_per_sublevel") or 180))
