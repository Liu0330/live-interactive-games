from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IngestStatus:
    connected: bool = False
    source: str = "mock"
    room_id: str = ""
    resolved_room_id: str = ""
    message: str = "未连接，可使用控制台模拟弹幕"
    last_error: str = ""
    chat_count: int = 0
    gift_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "source": self.source,
            "room_id": self.room_id,
            "resolved_room_id": self.resolved_room_id,
            "message": self.message,
            "last_error": self.last_error,
            "chat_count": self.chat_count,
            "gift_count": self.gift_count,
            "extra": self.extra,
        }


class IngestPlugin(ABC):
    """弹幕输入插件。主播只应连接自己的直播间。"""

    name = "base"

    @abstractmethod
    def start(self, room_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> IngestStatus:
        raise NotImplementedError
