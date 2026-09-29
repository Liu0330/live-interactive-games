from __future__ import annotations

import threading
import time
from typing import Any

from app.bus import ChatEvent, bus
from app.clock import pause as clock_pause
from app.clock import resume as clock_resume
from app.config import danmaku_settings, load_config, save_config
from app.engagement import get_engagement
from app.games.bomb import BombGame
from app.games.emoji_guess import EmojiGame
from app.games.idiom import IdiomGame, set_accept_listener
from app.games.lottery import LotteryGame
from app.games.quiz import QuizGame
from app.games.semantic import SemanticGame
from app.ranks import rank_title

GAMES = {
    "semantic": SemanticGame,
    "quiz": QuizGame,
    "bomb": BombGame,
    "lottery": LotteryGame,
    "idiom": IdiomGame,
    "emoji": EmojiGame,
}

GAME_LABELS = {
    "semantic": "语义猜词",
    "quiz": "弹幕答题",
    "bomb": "数字炸弹",
    "lottery": "弹幕抽奖",
    "idiom": "成语接龙",
    "emoji": "看图猜",
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
        self.feed: list[dict] = []
        self.feed_seq = 0
        self.held_chat: list[ChatEvent] = []
        self.paused = False
        self.engagement = get_engagement()
        self.restore_pause()
        from app.embed_cache import set_listener as set_embed_listener
        from app.related_cache import set_listener

        set_listener(self._on_related)
        set_embed_listener(self._on_embeds)
        set_accept_listener(self._on_idiom_late)
        bus.subscribe(self._on_event)

    @property
    def game(self):
        return self.games[self.active_id]

    def switch(self, game_id: str) -> None:
        if game_id not in self.games:
            raise ValueError("未知玩法")
        with self._lock:
            self.active_id = game_id
            self.held_chat.clear()
            save_config({"active_game": game_id})

    def start_round(self, specified: str = "") -> list[str]:
        with self._lock:
            notes = self._start_round_locked(specified)
            self._note_announce(notes)
            return notes

    def _start_round_locked(self, specified: str = "") -> list[str]:
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
        notes.extend(self._replay_held())
        return notes

    def _auto_continue(self) -> bool:
        return bool(load_config().get("auto_continue", True))

    def _count_gap_chat(self) -> bool:
        return bool(load_config().get("count_intermission_chat", True))

    def _ready_for_next(self, before: str, notes: list[str]) -> bool:
        game = self.game
        if game.status != "idle" or not self._auto_continue():
            return False
        if before == "reveal":
            return True
        return before == "playing" and "timeout" in notes

    def skip(self) -> list[str]:
        with self._lock:
            notes = self.game.skip()
            self._note_announce(notes)
            return notes

    def pause(self) -> None:
        with self._lock:
            if self.paused:
                return
            clock_pause()
            self.paused = True
            self._save_pause(True)

    def resume(self) -> None:
        with self._lock:
            if not self.paused:
                return
            delta = clock_resume()
            self._shift_clocks(delta)
            self.paused = False
            self._save_pause(False)

    def restore_pause(self) -> None:
        from app.db import meta_get

        if meta_get("paused") != "1" or self.paused:
            return
        clock_pause()
        self.paused = True

    def _save_pause(self, paused: bool) -> None:
        from app.db import meta_set

        meta_set("paused", "1" if paused else "0")

    def _shift_clocks(self, delta: float) -> None:
        if delta <= 0:
            return
        for game in self.games.values():
            if game.ends_at:
                game.ends_at += delta
            if game.reveal_until:
                game.reveal_until += delta
            hint_at = float(getattr(game, "next_hint_at", 0) or 0)
            if hint_at:
                game.next_hint_at = hint_at + delta
        bonus = float(getattr(self.engagement, "bonus_until", 0) or 0)
        if bonus:
            self.engagement.bonus_until = bonus + delta

    def tick(self) -> list[str]:
        with self._lock:
            if self.paused:
                return []
            before = self.game.status
            notes = self.game.tick()
            if hasattr(self.game, "maybe_hint"):
                notes.extend(self.game.maybe_hint())
            if self._ready_for_next(before, notes):
                notes.extend(self._start_round_locked(""))
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
            if self.paused:
                return
            game = self.games.get("semantic")
            if game is None or not hasattr(game, "rescore_embeddings"):
                return
            before = game.status
            game.rescore_embeddings()
            if game.status != before and game.announcement:
                self._note_announce(["announce", "win"])

    def _on_idiom_late(self, idiom: str, user_id: str, nickname: str, token: int) -> None:
        with self._lock:
            if self.paused:
                return
            game = self.games.get("idiom")
            if game is None or not hasattr(game, "accept_late"):
                return
            if not game.accept_late(idiom, user_id, nickname, token):
                return
            if self.active_id == "idiom":
                self._note_announce(["announce", "points"])

    def _on_related(self, secret: str, mapping: dict[str, float]) -> None:
        from app.games.similarity import normalize_word

        with self._lock:
            if self.paused:
                return
            game = self.games.get("semantic")
            if game is None or normalize_word(game.secret) != normalize_word(secret):
                return
            before = game.status
            game.absorb_related(mapping)
            if game.status != before and game.announcement:
                self._note_announce(["announce", "win"])

    def _on_event(self, event: ChatEvent) -> None:
        with self._lock:
            self._push_feed(event)
            if event.event_type == "chat" and self._take_suggestion(event):
                return
            if self.paused:
                return
            if (
                event.event_type == "chat"
                and self._count_gap_chat()
                and (
                    self.game.status == "reveal"
                    or (self.game.status == "idle" and self.game.round_no > 0)
                )
            ):
                self._hold_chat(event)
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
            if ("win" in notes or "points" in notes) and self.feed and self.feed[-1].get("id") == event.event_id:
                self.feed[-1]["style"] = "win"
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
            feed = list(self.feed)
            feed_seq = self.feed_seq
            from app.clock import now as clock_now

            intermission = max(0, int(game.reveal_until - clock_now())) if game.status == "reveal" else 0
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
        public["feed"] = feed
        public["feed_seq"] = feed_seq
        public["auto_continue"] = self._auto_continue()
        public["intermission"] = intermission
        public["paused"] = self.paused
        public["danmaku"] = danmaku_settings(cfg)
        return public

    def _take_suggestion(self, event: ChatEvent) -> bool:
        from app.banks import capture_suggestion

        return capture_suggestion(event.content, event.nickname, event.user_id, self.active_id) is not None

    def _hold_chat(self, event: ChatEvent) -> None:
        if not (event.content or "").strip():
            return
        self.held_chat.append(event)
        self.held_chat = self.held_chat[-20:]

    def _replay_held(self) -> list[str]:
        queued = list(self.held_chat)
        self.held_chat.clear()
        notes: list[str] = []
        for event in queued:
            if self._take_suggestion(event):
                continue
            if self.game.status != "playing":
                self.held_chat.append(event)
                continue
            notes.extend(self.game.on_comment(event))
        self.held_chat = self.held_chat[-20:]
        return notes

    def _push_feed(self, event: ChatEvent) -> None:
        now = time.time()
        kind = _feed_kind(event)
        text = _feed_text(event)
        if not text:
            return
        if kind == "like" and self.feed:
            last = self.feed[-1]
            if last.get("kind") == "like" and last.get("nickname") == event.nickname and now - float(last.get("ts") or 0) < 2:
                last["count"] = int(last.get("count") or 1) + max(1, int(event.like_count or event.gift_count or 1))
                last["text"] = f"点赞 +{last['count']}"
                last["ts"] = now
                self.feed_seq += 1
                return
        item = {
            "id": event.event_id,
            "nickname": event.nickname,
            "text": text[:40],
            "kind": kind,
            "ts": event.ts or now,
            "count": max(1, int(event.like_count or event.gift_count or 1)) if kind == "like" else 1,
        }
        self.feed.append(item)
        recent = [row for row in self.feed if row["kind"] in {"chat", "like"} and now - float(row["ts"]) < 1]
        overflow = len(recent) - 8
        if overflow > 0:
            kept: list[dict] = []
            for row in self.feed:
                if overflow and row in recent and row is not item and row["kind"] in {"chat", "like"}:
                    overflow -= 1
                    continue
                kept.append(row)
            self.feed = kept
        self.feed = self.feed[-30:]
        self.feed_seq += 1

    def host_status_text(self) -> str:
        with self._lock:
            extra = self.game.host_state()
        return extra.get("status_text") or f"当前玩法 {GAME_LABELS.get(self.active_id)}"


manager = GameManager()


def _is_like_gift(event: ChatEvent) -> bool:
    return event.event_type == "gift" and (event.gift_name or "").strip() == "点赞"


def _feed_kind(event: ChatEvent) -> str:
    if event.event_type == "gift" and not _is_like_gift(event):
        return "gift"
    if event.event_type == "like" or _is_like_gift(event):
        return "like"
    if event.event_type == "member":
        return "member"
    return "chat"


def _feed_text(event: ChatEvent) -> str:
    kind = _feed_kind(event)
    if kind == "gift":
        name = (event.gift_name or "礼物").strip()
        count = max(1, int(event.gift_count or 1))
        return f"送出 {name}×{count}"
    if kind == "like":
        count = max(1, int(event.like_count or event.gift_count or 1))
        return f"点赞 +{count}"
    if kind == "member":
        return "进入直播间"
    return (event.content or "").strip()


def preview_rank(points: int) -> str:
    cfg = load_config()
    return rank_title(points, cfg.get("rank_names"), int(cfg.get("points_per_sublevel") or 180))
