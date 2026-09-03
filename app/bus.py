from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class ChatEvent:
    nickname: str
    content: str
    source: str = "mock"
    user_id: str = ""
    gift_name: str = ""
    gift_count: int = 0
    event_type: str = "chat"
    ts: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        self.nickname = (self.nickname or "观众").strip() or "观众"
        self.content = (self.content or "").strip()
        if not self.user_id:
            self.user_id = self.nickname


Listener = Callable[[ChatEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._lock = Lock()
        self.recent: list[ChatEvent] = []

    def subscribe(self, fn: Listener) -> None:
        with self._lock:
            self._listeners.append(fn)

    def publish(self, event: ChatEvent) -> ChatEvent:
        with self._lock:
            self.recent.append(event)
            self.recent = self.recent[-80:]
            listeners = list(self._listeners)
        for fn in listeners:
            fn(event)
        return event


bus = EventBus()
