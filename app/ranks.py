from __future__ import annotations


DEFAULT_RANKS = ["青铜", "白银", "黄金", "铂金", "钻石", "星耀", "王者", "挑战者"]
SUBLEVELS = 5


def rank_title(points: int, rank_names: list[str] | None = None, per_sub: int = 180) -> str:
    names = rank_names or DEFAULT_RANKS
    if not names:
        names = DEFAULT_RANKS
    per_sub = max(1, int(per_sub))
    points = max(0, int(points))
    step = points // per_sub
    last = names[-1]
    max_step = (len(names) - 1) * SUBLEVELS
    if step >= max_step:
        extra = step - max_step
        return last if extra == 0 else f"{last}+{extra}"
    rank_i = step // SUBLEVELS
    sub = SUBLEVELS - (step % SUBLEVELS)
    return f"{names[rank_i]}{sub}"


def rank_icon(title: str) -> str:
    if title.startswith("挑战") or title.startswith("王者"):
        return "crown"
    if title.startswith("星耀") or title.startswith("钻石"):
        return "diamond"
    if title.startswith("铂金") or title.startswith("黄金"):
        return "gold"
    if title.startswith("白银"):
        return "silver"
    return "bronze"
