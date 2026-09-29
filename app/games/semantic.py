from __future__ import annotations

import random
import time
from typing import Any

from app.bus import ChatEvent
from app.config import load_config
from app.games.base import BaseGame
from app.scoring import grant_points, grant_win, win_suffix
from app.games.similarity import (
    blend_score,
    hint_neighbors,
    is_usable_guess,
    local_similarity,
    normalize_word,
    rank_guesses,
)
from app.games.wordbank import answer_len_label, classify_word, load_common_guesses, pick_word


def _hint_candidates(secret: str) -> list[str]:
    return hint_neighbors(secret)


class SemanticGame(BaseGame):
    game_id = "semantic"
    title = "挑战最强大脑"

    def __init__(self) -> None:
        super().__init__()
        self.secret = ""
        self.category = "名词"
        self.guesses: list[dict] = []
        self.hints: list[str] = []
        self.hint_pool: list[str] = []
        self.next_hint_at = 0.0
        self.likes = 0
        self.seq = 0
        self.winner = ""
        self.winner_word = ""
        self.max_score = 0.0
        self.embed_fn = None
        self.prepared = ""

    def _params(self) -> dict:
        return load_config().get("semantic", {})

    def start_round(self, specified: str = "") -> list[str]:
        params = self._params()
        self.round_no += 1
        length = int(params.get("answer_length") or 0)
        if (specified or "").strip():
            self.secret = pick_word(specified, length)
        elif self.prepared:
            self.secret = self.prepared
        else:
            self.secret = pick_word("", length)
        upcoming = pick_word("", length)
        self.prepared = upcoming if upcoming and upcoming != self.secret else ""
        self.category = classify_word(self.secret)
        self.guesses = []
        self.hints = []
        self.hint_pool = _hint_candidates(self.secret)
        self.status = "playing"
        self.started_at = time.time()
        countdown = int(params.get("countdown") or 180)
        self.ends_at = self.started_at + countdown
        self.next_hint_at = self.started_at + int(params.get("hint_interval") or 30)
        self.likes = 0
        self.seq = 0
        self.winner = ""
        self.winner_word = ""
        self.max_score = 0.0
        self.announcement = f"第{self.round_no}局开始，请在弹幕里发送词语"
        self.last_winner = ""
        return ["start", "announce"]

    def skip(self) -> list[str]:
        word = self.secret
        self.status = "idle"
        self.announcement = f"已跳过，上一词是 {word}" if word else "已跳过当前回合"
        return ["skip", "announce"]

    def on_timeout(self) -> list[str]:
        params = self._params()
        self.status = "reveal"
        self.reveal_until = time.time() + int(params.get("post_round_delay") or 8)
        self.announcement = f"时间到，答案是 {self.secret}"
        return ["timeout", "announce"]

    def maybe_hint(self, now: float | None = None) -> list[str]:
        now = now if now is not None else time.time()
        params = self._params()
        if self.status != "playing":
            return []
        limit = int(params.get("hints_per_round") or 3)
        if len(self.hints) >= limit:
            return []
        if now < self.next_hint_at:
            return []
        if self._push_hint():
            self.next_hint_at = now + int(params.get("hint_interval") or 30)
            return ["hint"]
        return []

    def _push_hint(self) -> bool:
        while self.hint_pool:
            word = self.hint_pool.pop(0)
            if word not in self.hints and word != normalize_word(self.secret):
                self.hints.append(word)
                return True
        return False

    def score_word(self, word: str) -> float:
        word = normalize_word(word)
        secret = normalize_word(self.secret)
        if not word:
            return 0.0
        local = local_similarity(word, secret)
        if self.embed_fn:
            try:
                return blend_score(local, float(self.embed_fn(word, secret)))
            except Exception:
                return local
        from app.config import llm_ready, load_config
        from app.related_cache import combine_scores, lookup_score, schedule_refine

        if not llm_ready():
            return local
        related = lookup_score(secret, word)
        threshold = float(self._params().get("hit_threshold") or 80)
        allow = bool(load_config().get("llm_related_can_win"))
        if related is None:
            schedule_refine(secret, word)
            return local
        return combine_scores(local, related, hit_threshold=threshold, allow_related_win=allow)

    def absorb_related(self, mapping: dict[str, float]) -> None:
        secret = normalize_word(self.secret)
        added: list[str] = []
        ranked = sorted(mapping.items(), key=lambda item: -float(item[1]))
        for raw, score in ranked:
            word = normalize_word(raw)
            if not word or word == secret or float(score) < 55:
                continue
            if word in self.hints or word in added:
                continue
            added.append(word)
        if added:
            rest = [word for word in self.hint_pool if word not in added and word != secret]
            self.hint_pool = added + rest
        if not self.embed_fn:
            self._rescore_with_cache()

    def _rescore_with_cache(self) -> None:
        from app.config import load_config
        from app.related_cache import combine_scores, get_related

        mapping = get_related(self.secret)
        if not mapping or not self.guesses:
            return
        threshold = float(self._params().get("hit_threshold") or 80)
        allow = bool(load_config().get("llm_related_can_win"))
        secret = normalize_word(self.secret)
        winner = None
        for row in self.guesses:
            word = normalize_word(str(row.get("word") or ""))
            if not word or word == secret:
                continue
            local = local_similarity(word, secret)
            row["score"] = combine_scores(
                local,
                mapping.get(word),
                hit_threshold=threshold,
                allow_related_win=allow,
            )
            if (
                allow
                and self.status == "playing"
                and not self.winner
                and float(row["score"]) + 1e-6 >= threshold
                and (winner is None or float(row["score"]) > float(winner["score"]))
            ):
                winner = row
        self.max_score = max(float(row["score"]) for row in self.guesses)
        if winner is not None:
            self._win(str(winner.get("nickname") or ""), str(winner.get("user_id") or ""), str(winner["word"]))

    def _record_guess(self, nickname: str, user_id: str, word: str, score: float, via: str = "chat") -> dict:
        self.seq += 1
        row = {
            "seq": self.seq,
            "nickname": nickname,
            "user_id": user_id,
            "word": word,
            "score": score,
            "via": via,
        }
        self.guesses.append(row)
        self.max_score = max(self.max_score, score)
        return row

    def on_comment(self, event: ChatEvent) -> list[str]:
        if self.status != "playing":
            return []
        self.last_award = None
        params = self._params()
        if not is_usable_guess(event.content, int(params.get("max_guess_chars") or 12)):
            return []
        word = normalize_word(event.content)
        if word == normalize_word(self.secret):
            # 命中按 100，但不把谜底提前展示给未结束的排名以外逻辑
            score = 100.0
        else:
            score = self.score_word(word)
        self._record_guess(event.nickname, event.user_id, word, score)
        notes = ["guess"]
        threshold = float(params.get("hit_threshold") or 80)
        if score + 1e-6 >= threshold or word == normalize_word(self.secret):
            self._win(event.nickname, event.user_id, word)
            notes.extend(["win", "announce"])
        elif score >= 70:
            self.last_award = grant_points(
                event.user_id,
                event.nickname,
                int(params.get("near_points") or 8),
                reason="semantic_near",
            )
            notes.append("points")
        return notes

    def _win(self, nickname: str, user_id: str, word: str) -> None:
        params = self._params()
        leftover = self.remaining()
        pts = int(params.get("win_points") or 120) + leftover
        award = grant_win(user_id, nickname, pts, reason="semantic")
        self.last_award = award
        self.winner = nickname
        self.winner_word = word
        self.last_winner = nickname
        self.status = "reveal"
        self.reveal_until = time.time() + int(params.get("post_round_delay") or 8)
        self.announcement = f"恭喜 {nickname} 猜中了，答案是 {self.secret}{win_suffix(award)}"

    def unlock_hint(self, nickname: str = "") -> str:
        if self.status != "playing":
            return ""
        if self._push_hint():
            return self.hints[-1]
        return ""

    def refresh_prompt(self) -> str:
        if self.status != "playing":
            return ""
        params = self._params()
        previous = self.secret
        nxt = previous
        for _ in range(8):
            nxt = pick_word("", int(params.get("answer_length") or 0))
            if nxt != previous:
                break
        self.secret = nxt
        self.category = classify_word(self.secret)
        self.guesses = []
        self.hints = []
        self.hint_pool = _hint_candidates(self.secret)
        self.max_score = 0.0
        self.winner = ""
        self.winner_word = ""
        from app.related_cache import schedule_prefetch

        schedule_prefetch(self.secret)
        return "已刷新本轮词语"

    def inject_random_words(self, nickname: str, user_id: str, count: int) -> int:
        if self.status != "playing":
            return 0
        n = min(400, max(1, int(count or 1)))
        secret = normalize_word(self.secret)
        pool = [w for w in load_common_guesses() if normalize_word(w) != secret]
        if not pool:
            return 0
        picked = random.sample(pool, k=min(n, len(pool)))
        for word in picked:
            score = local_similarity(word, self.secret)
            self._record_guess(nickname, user_id, word, score, via="gift")
        return len(picked)

    def on_gift(self, event: ChatEvent) -> list[str]:
        if self.status != "playing":
            return []
        action, count, threshold = _match_gift(event.gift_name)
        notes: list[str] = ["gift"]
        if action == "like":
            self.likes += max(1, event.gift_count or count)
            if threshold and self.likes >= threshold:
                self.likes = 0
                if self._push_hint():
                    notes.append("hint")
                    self.announcement = f"{event.nickname} 点赞触发了一条提示"
                    notes.append("announce")
            return notes
        if action in {"extra_hint", "hint"}:
            hinted = self.unlock_hint(event.nickname)
            if hinted:
                notes.append("hint")
                self.announcement = f"{event.nickname} 送出{event.gift_name}，解锁提示 {hinted}"
                notes.append("announce")
            return notes
        if action == "add_time":
            detail = self.add_time(count if count > 1 else 30)
            if detail:
                self.announcement = f"{event.nickname} 送出{event.gift_name}，{detail}"
                notes.append("announce")
            return notes
        if action == "refresh":
            detail = self.refresh_prompt()
            if detail:
                self.announcement = f"{event.nickname} 送出{event.gift_name}，{detail}"
                notes.append("announce")
            return notes
        if action == "random_words":
            added = self.inject_random_words(event.nickname, event.user_id, int(count) * max(1, event.gift_count or 1))
            if added:
                notes.append("guess")
                self.announcement = f"{event.nickname} 送出{event.gift_name}，注入 {added} 个随机词"
                notes.append("announce")
        return notes

    def public_state(self) -> dict[str, Any]:
        ranked = rank_guesses(self.guesses, limit=16)
        reveal = self.status == "reveal"
        return {
            "title": self.title,
            "round": self.round_no,
            "status": self.status,
            "countdown": self.remaining(),
            "category": self.category,
            "answer_len": answer_len_label(self.secret) if self.secret else "",
            "hints": list(self.hints),
            "guesses": ranked,
            "max_score": self.max_score,
            "likes": self.likes,
            "winner": self.winner,
            "reveal": self.secret if reveal else "",
            "announcement": self.announcement,
        }

    def host_state(self) -> dict[str, Any]:
        from app.config import scoring_info

        playing = "playing" if self.status == "playing" else self.status
        secret = self.secret or "—"
        info = scoring_info()
        return {
            "secret": self.secret,
            "status_text": f"当前回合正在 {playing} - 词语是 [ {secret} ]",
            "scoring_mode": info["scoring_mode"],
            "scoring_mode_label": info["scoring_mode_label"],
        }


def _match_gift(name: str) -> tuple[str, int, int]:
    name = (name or "").strip()
    gifts = load_config().get("gifts") or []
    for item in gifts:
        gname = str(item.get("name") or "")
        if gname and (gname in name or name in gname):
            return (
                str(item.get("action") or ""),
                int(item.get("count") or 1),
                int(item.get("threshold") or 0),
            )
    return "", 0, 0
