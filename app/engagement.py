from __future__ import annotations

import re
import time
from typing import Any

from app.bus import ChatEvent
from app.config import load_config
from app.db import init_db, leaderboard, leaderboard_period, meta_get, meta_set

ACTIONS = {"hint", "add_time", "skip", "refresh", "random_words"}

ACTION_LABELS = {
    "hint": "解锁提示",
    "add_time": "加时",
    "skip": "跳过当前",
    "refresh": "刷新题目",
    "random_words": "注入随机词",
}


def action_label(tier: dict | None) -> str:
    if not tier:
        return "感谢支持"
    action = str(tier.get("action") or "hint")
    if action == "add_time":
        seconds = int(tier.get("seconds") or 30)
        return f"加时 {seconds} 秒"
    return ACTION_LABELS.get(action, "感谢支持")


def match_tier(name: str, value: int, tiers: list[dict] | None) -> dict | None:
    tiers = list(tiers or [])
    if not tiers:
        return None
    gift = (name or "").strip()
    for tier in tiers:
        for raw in tier.get("names") or []:
            gname = str(raw).strip()
            if gname and gift and (gname in gift or gift in gname):
                return tier
    diamonds = int(value or 0)
    if diamonds <= 0:
        return tiers[0]
    chosen = None
    chosen_min = -1
    for tier in tiers:
        min_v = int(tier.get("min_value") or 0)
        max_v = int(tier.get("max_value") or 0)
        if diamonds < min_v:
            continue
        if max_v and diamonds > max_v:
            continue
        if min_v >= chosen_min:
            chosen = tier
            chosen_min = min_v
    return chosen or tiers[0]


def gift_rule_lines(cfg: dict | None = None) -> list[dict]:
    data = cfg or load_config()
    rules: list[dict] = []
    for tier in data.get("gift_tiers") or []:
        names = "、".join(str(n) for n in (tier.get("names") or []) if str(n).strip())
        rules.append(
            {
                "name": names or str(tier.get("label") or "礼物"),
                "label": action_label(tier),
                "tier": tier.get("id") or "",
            }
        )
    likes = data.get("likes") or {}
    target = int(likes.get("target") or 100)
    if str(likes.get("reward") or "hint") == "bonus":
        seconds = int(likes.get("bonus_seconds") or 45)
        mult = int(likes.get("multiplier") or 2)
        reward = f"{mult} 倍积分 {seconds} 秒"
    else:
        reward = "解锁提示"
    rules.append({"name": f"点赞满 {target}", "label": reward, "tier": "like"})
    return rules


def _split_names(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,，\n]+", value)
    elif isinstance(value, list):
        parts = value
    else:
        parts = []
    out: list[str] = []
    for item in parts:
        name = str(item).strip()
        if name and name not in out:
            out.append(name)
        if len(out) >= 20:
            break
    return out


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def sanitize_gift_tiers(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for index, item in enumerate(raw[:4]):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "hint")
        if action not in ACTIONS:
            action = "hint"
        default_id = "small" if index == 0 else "big" if index == 1 else f"tier{index}"
        default_label = "小礼物" if index == 0 else "大礼物" if index == 1 else f"档位{index + 1}"
        out.append(
            {
                "id": str(item.get("id") or default_id)[:16],
                "label": str(item.get("label") or default_label)[:12],
                "names": _split_names(item.get("names")),
                "min_value": _as_int(item.get("min_value"), 0, 0, 1000000),
                "max_value": _as_int(item.get("max_value"), 0, 0, 1000000),
                "action": action,
                "seconds": _as_int(item.get("seconds"), 30, 5, 600),
                "count": _as_int(item.get("count"), 30, 1, 400),
            }
        )
    return out


def sanitize_likes(raw: Any) -> dict:
    data = raw if isinstance(raw, dict) else {}
    reward = "bonus" if str(data.get("reward") or "") == "bonus" else "hint"
    return {
        "target": _as_int(data.get("target"), 100, 1, 100000),
        "reward": reward,
        "bonus_seconds": _as_int(data.get("bonus_seconds"), 45, 5, 600),
        "multiplier": _as_int(data.get("multiplier"), 2, 2, 5),
    }


def sanitize_streak(raw: Any) -> dict:
    data = raw if isinstance(raw, dict) else {}
    return {
        "bonus_per": _as_int(data.get("bonus_per"), 15, 0, 500),
        "max_bonus": _as_int(data.get("max_bonus"), 60, 0, 2000),
    }


class Engagement:
    def __init__(self) -> None:
        self.likes = 0
        self.bonus_until = 0.0
        self.pending_hints = 0
        self.effects: list[dict] = []
        self.effect_seq = 0
        self.welcome: dict[str, Any] = {}
        self.welcome_seq = 0
        self.combo: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        init_db()
        self.likes = int(meta_get("likes", "0") or 0)
        self.bonus_until = float(meta_get("bonus_until", "0") or 0)
        self.pending_hints = int(meta_get("pending_hints", "0") or 0)

    def _persist(self) -> None:
        meta_set("likes", str(self.likes))
        meta_set("bonus_until", str(self.bonus_until))
        meta_set("pending_hints", str(self.pending_hints))

    def _tiers(self) -> list[dict]:
        return list(load_config().get("gift_tiers") or [])

    def _likes_cfg(self) -> dict:
        return dict(load_config().get("likes") or {})

    def multiplier(self) -> int:
        if time.time() >= self.bonus_until:
            return 1
        mult = int(self._likes_cfg().get("multiplier") or 2)
        return max(2, mult)

    def push_effect(
        self,
        kind: str,
        nickname: str,
        headline: str,
        detail: str,
        tier: str = "",
        speech: str = "",
    ) -> dict:
        self.effect_seq += 1
        item = {
            "seq": self.effect_seq,
            "kind": kind,
            "tier": tier,
            "nickname": nickname,
            "headline": headline,
            "detail": detail,
            "speech": speech,
        }
        self.effects.append(item)
        self.effects = self.effects[-8:]
        return item

    def note_award(self, nickname: str, award: dict | None) -> None:
        if not award:
            return
        streak = int(award.get("streak") or 0)
        if streak >= 2:
            self.combo = {"nickname": nickname, "streak": streak, "seq": self.effect_seq + 1}
        if award.get("leveled_up"):
            title = str(award.get("rank_name") or "")
            self.push_effect(
                kind="level",
                nickname=nickname,
                headline=f"{nickname} 升级",
                detail=title,
                speech=f"恭喜{nickname}升至{title}",
            )

    def on_member(self, event: ChatEvent) -> list[str]:
        self.welcome_seq += 1
        self.welcome = {
            "seq": self.welcome_seq,
            "nickname": event.nickname,
            "text": f"欢迎 {event.nickname} 进入直播间",
        }
        return ["member"]

    def on_likes(self, game: Any, nickname: str, count: int) -> list[str]:
        count = max(1, int(count or 1))
        self.likes += count
        cfg = self._likes_cfg()
        target = max(1, int(cfg.get("target") or 100))
        fired = 0
        while self.likes >= target and fired < 20:
            self.likes -= target
            fired += 1
        notes = ["like"]
        if fired:
            notes.extend(self._grant_like_reward(game, nickname, cfg, fired))
        self._persist()
        return notes

    def _grant_like_reward(self, game: Any, nickname: str, cfg: dict, times: int) -> list[str]:
        reward = str(cfg.get("reward") or "hint")
        if reward == "bonus":
            seconds = int(cfg.get("bonus_seconds") or 45) * times
            seconds = min(seconds, 600)
            now = time.time()
            base = self.bonus_until if self.bonus_until > now else now
            self.bonus_until = base + seconds
            mult = max(2, int(cfg.get("multiplier") or 2))
            detail = f"开启 {mult} 倍积分 {seconds} 秒"
            speech = f"点赞进度已满，{detail}"
        else:
            hints: list[str] = []
            for _ in range(min(times, 3)):
                text = self._apply_hint(game)
                if text:
                    hints.append(text)
            detail = "、".join(hints) if hints else "进度已满"
            speech = f"点赞进度已满，{detail}"
        if hasattr(game, "announcement"):
            game.announcement = speech
        self.push_effect(
            kind="like",
            nickname=nickname or "直播间",
            headline="点赞进度已满",
            detail=detail,
            tier="like",
            speech=speech,
        )
        self._persist()
        return ["like_reward", "announce", "effect"]

    def apply_pending(self, game: Any) -> list[str]:
        if self.pending_hints <= 0 or getattr(game, "status", "") != "playing":
            return []
        used = 0
        while self.pending_hints > 0 and used < 3:
            text = game.unlock_hint("") if hasattr(game, "unlock_hint") else ""
            if not text:
                break
            self.pending_hints -= 1
            used += 1
        self._persist()
        if not used:
            return []
        extra = f"点赞奖励解锁了 {used} 条提示"
        current = getattr(game, "announcement", "") or ""
        game.announcement = f"{current}，{extra}" if current else extra
        return ["hint", "announce"]

    def on_gift(self, game: Any, event: ChatEvent) -> list[str]:
        tier = match_tier(event.gift_name, event.gift_value, self._tiers())
        action = str((tier or {}).get("action") or "hint")
        if getattr(game, "game_id", "") == "lottery":
            detail = self._lottery_gift(game, event, action, tier or {})
        else:
            detail = self._apply_action(game, event, action, tier or {})
        gift_name = event.gift_name or "礼物"
        speech = f"感谢{event.nickname}送出的{gift_name}"
        if detail:
            speech = f"{speech}，{detail}"
        if hasattr(game, "announcement"):
            game.announcement = speech
        self.push_effect(
            kind="gift",
            nickname=event.nickname,
            headline=f"感谢 {event.nickname}",
            detail=f"{gift_name} · {detail or '感谢支持'}",
            tier=str((tier or {}).get("id") or ""),
            speech=speech,
        )
        return ["gift", "announce", "effect"]

    def _lottery_gift(self, game: Any, event: ChatEvent, action: str, tier: dict) -> str:
        if action == "refresh":
            game.refresh_prompt()
            game.on_gift(event)
            return "已刷新本轮抽奖，并计入权重"
        if action == "skip":
            game.on_gift(event)
            game.draw()
            text = getattr(game, "announcement", "") or "立即开奖"
            return text
        game.on_gift(event)
        if action == "add_time":
            return game.add_time(int(tier.get("seconds") or 30)) or "已提高中奖权重"
        if action == "random_words":
            return "已提高中奖权重"
        hinted = game.unlock_hint(event.nickname) if hasattr(game, "unlock_hint") else ""
        return hinted or "已提高中奖权重"

    def _apply_action(self, game: Any, event: ChatEvent, action: str, tier: dict) -> str:
        if getattr(game, "status", "") != "playing" and action in {"hint", "add_time", "refresh", "random_words"}:
            if action == "hint":
                self.pending_hints += 1
                self._persist()
                return "本局未开始，提示留到下回合"
            return "当前没有进行中的回合"
        if action == "add_time":
            return game.add_time(int(tier.get("seconds") or 30)) or "当前没有进行中的回合"
        if action == "skip":
            game.skip()
            return "已跳过当前回合"
        if action == "refresh":
            return game.refresh_prompt() or "当前没有进行中的回合"
        if action == "random_words" and hasattr(game, "inject_random_words"):
            count = int(tier.get("count") or 30) * max(1, int(event.gift_count or 1))
            added = game.inject_random_words(event.nickname, event.user_id, count)
            return f"注入 {added} 个随机词" if added else "没有可注入的词"
        return self._apply_hint(game)

    def _apply_hint(self, game: Any) -> str:
        if getattr(game, "status", "") != "playing":
            self.pending_hints += 1
            self._persist()
            return "本局未开始，提示留到下回合"
        text = game.unlock_hint("") if hasattr(game, "unlock_hint") else ""
        if not text:
            self.pending_hints += 1
            self._persist()
            return "当前没有更多提示，已留给下回合"
        return f"解锁提示 {text}"

    def public_view(self) -> dict[str, Any]:
        cfg = load_config()
        likes_cfg = cfg.get("likes") or {}
        target = max(1, int(likes_cfg.get("target") or 100))
        remaining = max(0, int(self.bonus_until - time.time()))
        mult = self.multiplier() if remaining else 1
        names = cfg.get("rank_names")
        per_sub = int(cfg.get("points_per_sublevel") or 180)
        boards = {
            "all": {"label": "总榜", "rows": leaderboard(8, names, per_sub)},
            "day": {"label": "日榜", "rows": leaderboard_period("day", 8, names, per_sub)},
            "week": {"label": "周榜", "rows": leaderboard_period("week", 8, names, per_sub)},
        }
        reward = str(likes_cfg.get("reward") or "hint")
        if reward == "bonus":
            reward_label = f"满赞 {mult if remaining else int(likes_cfg.get('multiplier') or 2)} 倍积分"
        else:
            reward_label = "满赞解锁提示"
        return {
            "like_bar": {
                "count": self.likes,
                "target": target,
                "reward": reward,
                "reward_label": reward_label,
                "percent": min(100, int(self.likes * 100 / target)),
            },
            "bonus": {
                "active": remaining > 0,
                "remaining": remaining,
                "multiplier": mult,
                "label": f"{mult} 倍积分" if remaining else "",
            },
            "effects": list(self.effects),
            "effect_seq": self.effect_seq,
            "welcome": dict(self.welcome),
            "combo": dict(self.combo),
            "gift_rules": gift_rule_lines(cfg),
            "boards": boards,
        }


_engagement: Engagement | None = None


def get_engagement() -> Engagement:
    global _engagement
    if _engagement is None:
        _engagement = Engagement()
    return _engagement
