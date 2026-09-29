from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable

from app.bus import ChatEvent
from app.config import chat_ready, load_config
from app.games.base import BaseGame
from app.games.idiom_bank import (
    all_idioms,
    clean_idiom,
    continuations,
    links,
    remember_idiom,
)
from app.scoring import grant_win, note_miss, win_suffix

LateFn = Callable[[str, str, str, int], None]
_late: LateFn | None = None


def set_accept_listener(fn: LateFn | None) -> None:
    global _late
    _late = fn


def judge_idiom(idiom: str) -> bool:
    from app.llm import chat_completion
    from app.minimax import strip_think

    word = clean_idiom(idiom)
    if not word:
        return False
    text = chat_completion(
        [
            {
                "role": "system",
                "content": "你只判断一个四字词是不是常见、健康、适合全年龄直播的中文成语。不确定、生造、低俗或敏感内容都回答 no。只回复 yes 或 no。",
            },
            {"role": "user", "content": word},
        ],
        timeout=12,
        temperature=0,
        max_tokens=8,
    )
    reply = strip_think(text).strip().lower()
    if reply.startswith("no") or reply.startswith("不"):
        return False
    return reply.startswith("yes") or reply.startswith("是")


def schedule_idiom_check(idiom: str, user_id: str, nickname: str, token: int) -> None:
    if not chat_ready() or not clean_idiom(idiom):
        return

    def run() -> None:
        try:
            ok = judge_idiom(idiom)
        except Exception:
            return
        if not ok:
            return
        remember_idiom(idiom)
        fn = _late
        if fn:
            try:
                fn(clean_idiom(idiom), user_id, nickname, token)
            except Exception:
                return

    threading.Thread(target=run, name="idiom-check", daemon=True).start()


class IdiomGame(BaseGame):
    game_id = "idiom"
    title = "成语接龙"

    def __init__(self) -> None:
        super().__init__()
        self.head = ""
        self.used: set[str] = set()
        self.chain: list[str] = []
        self.hints: list[str] = []
        self.links_log: list[dict] = []
        self.link_token = 0
        self.hint_example = ""

    def _params(self) -> dict:
        return load_config().get("idiom") or {}

    def _allow_pinyin(self) -> bool:
        return bool(self._params().get("allow_pinyin"))

    def start_round(self, specified: str = "") -> list[str]:
        self.round_no += 1
        self.used = set()
        self.chain = []
        self.hints = []
        self.links_log = []
        self.hint_example = ""
        self.status = "playing"
        word = clean_idiom(specified)
        if not word:
            word = self._pick_start()
        self._set_head(word, f"成语接龙开始，从「{word}」接龙")
        return ["start", "announce"]

    def _pick_start(self) -> str:
        pool = [word for word in all_idioms() if word not in self.used]
        linked = [word for word in pool if continuations(word, self._allow_pinyin(), self.used | {word})]
        choice = linked or pool
        if not choice:
            return "一心一意"
        return random.choice(choice)

    def _set_head(self, word: str, announcement: str) -> None:
        self.head = word
        self.used.add(word)
        self.chain = [word]
        self.hints = []
        self.hint_example = ""
        self.link_token += 1
        self.status = "playing"
        self.started_at = time.time()
        seconds = int(self._params().get("link_seconds") or 30)
        self.ends_at = time.time() + max(8, seconds)
        self.announcement = announcement

    def _offer_new(self, reason: str) -> str:
        nxt = self._pick_start()
        if nxt in self.used and len(all_idioms() - self.used) == 0:
            self.status = "idle"
            self.announcement = "这局能接的成语用完了"
            return self.announcement
        self._set_head(nxt, f"{reason}：{nxt}")
        return self.announcement

    def on_timeout(self) -> list[str]:
        self._offer_new("时间到，没人接上，换一个新开头")
        return ["timeout", "announce"]

    def skip(self) -> list[str]:
        if self.status != "playing":
            self._offer_new("换一个新开头")
            self.round_no = max(self.round_no, 1)
        else:
            self._offer_new("已换一个新开头")
        return ["skip", "announce"]

    def refresh_prompt(self) -> str:
        if self.status != "playing" and not self.head:
            return ""
        self.status = "playing"
        return self._offer_new("已换一个新开头")

    def unlock_hint(self, nickname: str = "") -> str:
        if self.status != "playing" or not self.head:
            return ""
        options = continuations(self.head, self._allow_pinyin(), self.used)
        if not options:
            return ""
        if not self.hint_example or self.hint_example in self.used:
            self.hint_example = options[0]
        shown = len(self.hints) + 1
        shown = min(4, shown)
        masked = self.hint_example[:shown] + "□" * (4 - shown)
        if masked not in self.hints:
            self.hints.append(masked)
        return masked

    def on_comment(self, event: ChatEvent) -> list[str]:
        if self.status != "playing" or not self.head:
            return []
        word = clean_idiom(event.content)
        if not word:
            return []
        if word in self.used or word == self.head:
            self.announcement = f"「{word}」这局已经用过了"
            return ["guess", "announce"]
        if not links(self.head, word, self._allow_pinyin()):
            if word in all_idioms():
                note_miss(event.user_id, event.nickname)
                self.announcement = f"{event.nickname} 的「{word}」接不上「{self.head[-1]}」"
                return ["guess", "announce"]
            return []
        if word not in all_idioms():
            schedule_idiom_check(word, event.user_id, event.nickname, self.link_token)
            self.announcement = f"「{word}」不在词表里，正在核对"
            return ["guess", "announce"]
        self._accept(event.nickname, event.user_id, word)
        return ["guess", "announce", "points"]

    def accept_late(self, idiom: str, user_id: str, nickname: str, token: int) -> bool:
        word = clean_idiom(idiom)
        if self.status != "playing" or token != self.link_token or not word:
            return False
        if word in self.used or not links(self.head, word, self._allow_pinyin()):
            return False
        if word not in all_idioms():
            return False
        self._accept(nickname, user_id, word)
        return True

    def _accept(self, nickname: str, user_id: str, word: str) -> None:
        points = int(self._params().get("win_points") or 30)
        award = grant_win(user_id, nickname, points, reason="idiom")
        self.last_award = award
        self.used.add(word)
        self.head = word
        self.chain.append(word)
        self.chain = self.chain[-8:]
        self.hints = []
        self.hint_example = ""
        self.link_token += 1
        seconds = int(self._params().get("link_seconds") or 30)
        self.ends_at = time.time() + max(8, seconds)
        self.links_log.append({"nickname": nickname, "idiom": word})
        self.links_log = self.links_log[-8:]
        self.last_winner = nickname
        self.announcement = f"{nickname} 接上了「{word}」{win_suffix(award)}"

    def public_state(self) -> dict[str, Any]:
        tail = self.head[-1] if self.head else ""
        return {
            "title": self.title,
            "round": self.round_no,
            "status": self.status,
            "countdown": self.remaining(),
            "head": self.head,
            "need": tail,
            "chain": list(self.chain),
            "hints": list(self.hints),
            "links": list(self.links_log),
            "allow_pinyin": self._allow_pinyin(),
            "announcement": self.announcement,
            "winner": self.last_winner,
            "reveal": "",
        }

    def host_state(self) -> dict[str, Any]:
        mode = "谐音可接" if self._allow_pinyin() else "必须同字"
        return {"status_text": f"成语接龙 {mode} - 当前 [{self.head or '—'}]"}
