from __future__ import annotations

from app.config import load_config
from app.db import add_points, add_score_event, get_user, set_streak
from app.ranks import rank_title


def multiplier() -> int:
    from app.engagement import get_engagement

    return get_engagement().multiplier()


def _rank_args(cfg: dict | None = None) -> tuple[list[str] | None, int]:
    data = cfg or load_config()
    per_sub = int(data.get("points_per_sublevel") or 180)
    names = data.get("rank_names")
    return names, per_sub


def win_suffix(award: dict | None) -> str:
    if not award:
        return ""
    bits: list[str] = []
    streak = int(award.get("streak") or 0)
    if streak >= 2:
        bits.append(f"{streak} 连击")
    bonus = int(award.get("streak_bonus") or 0)
    if bonus:
        bits.append(f"连击奖励 +{bonus}")
    mult = int(award.get("multiplier") or 1)
    if mult > 1:
        bits.append(f"{mult} 倍积分")
    if award.get("leveled_up") and award.get("rank_name"):
        bits.append(f"升至{award['rank_name']}")
    if not bits:
        return ""
    return "，" + "，".join(bits)


def _finish(user_id: str, nickname: str, base: int, bonus: int, streak: int, reason: str) -> dict:
    cfg = load_config()
    names, per_sub = _rank_args(cfg)
    before = get_user(user_id)
    prev_points = int(before.get("points") or 0)
    prev_rank = rank_title(prev_points, names, per_sub)
    mult = multiplier()
    total = (max(0, int(base)) + max(0, int(bonus))) * mult
    if total:
        points = add_points(user_id, nickname, total)
        add_score_event(user_id, nickname, total, reason)
    else:
        points = prev_points
    set_streak(user_id, nickname, streak)
    new_rank = rank_title(points, names, per_sub)
    return {
        "points_awarded": total,
        "base": int(base),
        "streak_bonus": int(bonus),
        "multiplier": mult,
        "streak": int(streak),
        "total_points": points,
        "rank_name": new_rank,
        "previous_rank": prev_rank,
        "leveled_up": new_rank != prev_rank,
        "nickname": nickname,
        "user_id": user_id,
    }


def account_id(user_id: str, nickname: str = "") -> str:
    """全玩法共用一个账号：优先抖音用户 ID，没有 ID 时用昵称。"""
    uid = (user_id or "").strip()
    nick = (nickname or "").strip()
    return uid or nick or "观众"


def grant_win(user_id: str, nickname: str, base_points: int, reason: str = "win") -> dict:
    user_id = account_id(user_id, nickname)
    user = get_user(user_id)
    streak = int(user.get("streak") or 0) + 1
    cfg = load_config().get("streak") or {}
    per = max(0, int(cfg.get("bonus_per") or 0))
    cap = max(0, int(cfg.get("max_bonus") or 0))
    bonus = min(cap, per * (streak - 1)) if streak > 1 else 0
    return _finish(user_id, nickname, base_points, bonus, streak, reason)


def grant_points(user_id: str, nickname: str, base_points: int, reason: str = "points") -> dict:
    user_id = account_id(user_id, nickname)
    user = get_user(user_id)
    streak = int(user.get("streak") or 0)
    return _finish(user_id, nickname, base_points, 0, streak, reason)


def note_miss(user_id: str, nickname: str) -> None:
    user_id = account_id(user_id, nickname)
    user = get_user(user_id)
    if int(user.get("streak") or 0) <= 0 and not user.get("exists"):
        return
    set_streak(user_id, nickname, 0)
