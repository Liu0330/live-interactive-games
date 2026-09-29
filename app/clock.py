"""直播时钟。暂停时停在按下的那一刻，继续时把这段时间补回倒计时。"""

from __future__ import annotations

import time

_paused = False
_mark = 0.0


def is_paused() -> bool:
    return _paused


def now() -> float:
    return _mark if _paused else time.time()


def pause() -> None:
    global _paused, _mark
    if _paused:
        return
    _mark = time.time()
    _paused = True


def resume() -> float:
    global _paused
    if not _paused:
        return 0.0
    delta = max(0.0, time.time() - _mark)
    _paused = False
    return delta


def reset() -> None:
    global _paused, _mark
    _paused = False
    _mark = 0.0
