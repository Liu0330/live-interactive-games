from __future__ import annotations

from app.bus import ChatEvent, bus
from app.ingest.base import IngestPlugin, IngestStatus


class MockIngest(IngestPlugin):
    name = "mock"

    def __init__(self) -> None:
        self._status = IngestStatus(source="mock", message="模拟器就绪，可在控制台发送测试弹幕")

    def start(self, room_id: str = "") -> None:
        self._status.room_id = room_id
        self._status.message = "模拟器运行中"

    def stop(self) -> None:
        self._status.connected = False
        self._status.message = "模拟器已停止"

    def status(self) -> IngestStatus:
        return self._status

    def inject_chat(self, nickname: str, content: str) -> ChatEvent:
        event = ChatEvent(nickname=nickname, content=content, source="mock", event_type="chat")
        bus.publish(event)
        self._status.chat_count += 1
        return event

    def inject_gift(
        self,
        nickname: str,
        gift_name: str,
        count: int = 1,
        gift_value: int = 0,
    ) -> ChatEvent:
        event = ChatEvent(
            nickname=nickname,
            content=gift_name,
            source="mock",
            event_type="gift",
            gift_name=gift_name,
            gift_count=max(1, int(count or 1)),
            gift_value=max(0, int(gift_value or 0)),
        )
        bus.publish(event)
        self._status.gift_count += 1
        return event

    def inject_like(self, nickname: str, count: int = 1) -> ChatEvent:
        likes = max(1, int(count or 1))
        event = ChatEvent(
            nickname=nickname,
            content="点赞",
            source="mock",
            event_type="like",
            gift_name="点赞",
            gift_count=likes,
            like_count=likes,
        )
        bus.publish(event)
        self._status.like_count += likes
        return event

    def inject_member(self, nickname: str) -> ChatEvent:
        event = ChatEvent(
            nickname=nickname,
            content="来了",
            source="mock",
            event_type="member",
        )
        bus.publish(event)
        self._status.member_count += 1
        return event


mock_ingest = MockIngest()
