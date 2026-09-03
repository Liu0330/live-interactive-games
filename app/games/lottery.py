from __future__ import annotations

import random
import time
from typing import Any

from app.bus import ChatEvent
from app.config import load_config
from app.db import add_points
from app.games.base import BaseGame


def unique_participants(entries: list[dict]) -> list[dict]:
    """同一用户只保留第一条有效记录，但累计礼物权重。"""
    by_user: dict[str, dict] = {}
    order: list[str] = []
    for item in entries:
        uid = str(item.get("user_id") or item.get("nickname") or "")
        if not uid:
            continue
        if uid not in by_user:
            row = dict(item)
            row["weight"] = max(1, int(item.get("weight") or 1))
            by_user[uid] = row
            order.append(uid)
        else:
            extra = int(item.get("weight") or 0) if item.get("gift") else 0
            by_user[uid]["weight"] = int(by_user[uid].get("weight") or 1) + extra
            if item.get("gift"):
                by_user[uid]["gift"] = True
    return [by_user[uid] for uid in order]


def pick_weighted(participants: list[dict], rng: random.Random | None = None) -> dict | None:
    if not participants:
        return None
    rng = rng or random.Random()
    weights = [max(1, int(p.get("weight") or 1)) for p in participants]
    return rng.choices(participants, weights=weights, k=1)[0]


class LotteryGame(BaseGame):
    game_id = "lottery"
    title = "弹幕抽奖"

    def __init__(self) -> None:
        super().__init__()
        self.keyword = "抽奖"
        self.entries: list[dict] = []
        self.winner: dict | None = None
        self.rolling = False

    def _params(self) -> dict:
        return load_config().get("lottery", {})

    def start_round(self, specified: str = "") -> list[str]:
        params = self._params()
        self.keyword = (specified or params.get("keyword") or "抽奖").strip() or "抽奖"
        self.round_no += 1
        self.entries = []
        self.winner = None
        self.rolling = False
        self.status = "playing"
        self.started_at = time.time()
        self.ends_at = self.started_at + int(params.get("duration") or 60)
        self.announcement = f"发送「{self.keyword}」参与抽奖"
        return ["start", "announce"]

    def on_timeout(self) -> list[str]:
        return self.draw()

    def skip(self) -> list[str]:
        self.status = "idle"
        self.announcement = "已取消本轮抽奖"
        return ["skip", "announce"]

    def qualifies(self, text: str) -> bool:
        return self.keyword in (text or "").strip()

    def on_comment(self, event: ChatEvent) -> list[str]:
        if self.status != "playing":
            return []
        if not self.qualifies(event.content):
            return []
        self.entries.append(
            {
                "user_id": event.user_id,
                "nickname": event.nickname,
                "text": event.content.strip(),
                "weight": 1,
                "gift": False,
                "ts": event.ts,
            }
        )
        return ["guess"]

    def on_gift(self, event: ChatEvent) -> list[str]:
        if self.status != "playing":
            return []
        if not load_config().get("lottery", {}).get("gift_weight", True):
            return []
        weight = max(1, int(event.gift_count or 1)) * 5
        self.entries.append(
            {
                "user_id": event.user_id,
                "nickname": event.nickname,
                "text": event.gift_name or "礼物",
                "weight": weight,
                "gift": True,
                "ts": event.ts,
            }
        )
        return ["gift"]

    def draw(self) -> list[str]:
        people = unique_participants(self.entries)
        self.winner = pick_weighted(people)
        self.rolling = True
        self.status = "reveal"
        self.reveal_until = time.time() + 8
        if self.winner:
            add_points(
                self.winner["user_id"],
                self.winner["nickname"],
                int(self._params().get("win_points") or 50),
            )
            self.last_winner = self.winner["nickname"]
            self.announcement = f"恭喜 {self.winner['nickname']} 中奖"
        else:
            self.announcement = "本轮没有人参与"
        return ["win", "announce"]

    def public_state(self) -> dict[str, Any]:
        people = unique_participants(self.entries)
        return {
            "title": self.title,
            "round": self.round_no,
            "status": self.status,
            "countdown": self.remaining(),
            "keyword": self.keyword,
            "participants": people,
            "count": len(people),
            "winner": self.winner,
            "rolling": self.rolling and self.status == "reveal",
            "reveal": (self.winner or {}).get("nickname") if self.status == "reveal" else "",
            "announcement": self.announcement,
        }

    def host_state(self) -> dict[str, Any]:
        name = (self.winner or {}).get("nickname") or "尚未开奖"
        return {
            "secret": self.keyword,
            "status_text": f"当前回合正在 {self.status} - 口令 [ {self.keyword} ]，中奖 [ {name} ]",
        }
