from __future__ import annotations

import gzip
import json
import re
import threading
import time
import urllib.parse
from typing import Any

import httpx

from app.bus import ChatEvent, bus
from app.ingest.base import IngestPlugin, IngestStatus
from app.ingest.protobuf_lite import decode_fields, first_bytes, first_int, first_str

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def extract_room_token(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    match = re.search(r"live\.douyin\.com/([A-Za-z0-9_]+)", value)
    if match:
        return match.group(1)
    return re.sub(r"[^\w\-]", "", value)


class DouyinRoomIngest(IngestPlugin):
    """主播用房间号读取自己直播间的公开弹幕。

    抖音网页直播协议会不定期改签名，因此这里：
    1) 解析 live.douyin.com/<id> 拿到 roomId / ttwid
    2) 尝试公开 webcast HTTP 拉取
    3) 再尝试 websocket 推送
    失败时控制台显示状态，主播可继续用模拟弹幕。
    """

    name = "douyin"

    def __init__(self) -> None:
        self._status = IngestStatus(source="douyin")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def status(self) -> IngestStatus:
        with self._lock:
            return IngestStatus(**self._status.as_dict())

    def start(self, room_id: str) -> None:
        token = extract_room_token(room_id)
        if not token:
            raise ValueError("请填写 live.douyin.com 后的数字房间号")
        self.stop()
        self._stop = threading.Event()
        with self._lock:
            self._status = IngestStatus(
                connected=False,
                source="douyin",
                room_id=token,
                message="正在解析直播间…",
            )
        self._thread = threading.Thread(target=self._run, args=(token,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        with self._lock:
            self._status.connected = False
            if not self._status.last_error:
                self._status.message = "已断开直连"

    def _set(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._status, key, value)

    def _run(self, token: str) -> None:
        try:
            room = _resolve_room(token)
            self._set(
                resolved_room_id=room.get("room_id") or "",
                extra={"title": room.get("title") or "", "status": room.get("status")},
                message="已解析房间，正在拉取公开弹幕…",
            )
            if not room.get("room_id"):
                self._set(
                    last_error="未能从页面解析到 roomId，请确认正在直播且房间号正确",
                    message="直连失败：无法解析房间",
                )
                return
            ok = self._poll_http(room)
            if not ok and not self._stop.is_set():
                self._listen_ws(room)
        except Exception as exc:
            self._set(last_error=str(exc), message=f"直连异常：{exc}", connected=False)

    def _poll_http(self, room: dict) -> bool:
        cursor = ""
        got_any = False
        consecutive_fail = 0
        url = "https://live.douyin.com/webcast/im/fetch/"
        headers = {
            "User-Agent": UA,
            "Referer": f"https://live.douyin.com/{room.get('web_rid') or room.get('room_id')}",
        }
        cookies = {}
        if room.get("ttwid"):
            cookies["ttwid"] = room["ttwid"]
        with httpx.Client(timeout=15, headers=headers, cookies=cookies, follow_redirects=True) as client:
            while not self._stop.is_set() and consecutive_fail < 6:
                params = {
                    "aid": "6383",
                    "live_id": "1",
                    "device_platform": "web",
                    "language": "zh-CN",
                    "room_id": room["room_id"],
                    "resp_content_type": "protobuf",
                    "cursor": cursor,
                    "last_rtt": "0",
                    "identity": "audience",
                }
                try:
                    resp = client.get(url, params=params)
                    if resp.status_code != 200 or not resp.content:
                        consecutive_fail += 1
                        time.sleep(1.5)
                        continue
                    parsed = _parse_push_payload(resp.content)
                    cursor = parsed.get("cursor") or cursor
                    if parsed["events"]:
                        got_any = True
                        consecutive_fail = 0
                        self._emit(parsed["events"])
                        self._set(
                            connected=True,
                            message=f"已连接（HTTP 拉取）房间 {room['room_id']}",
                            last_error="",
                        )
                    else:
                        if resp.headers.get("content-type", "").startswith("application/json"):
                            consecutive_fail += 1
                        time.sleep(1.2)
                except Exception:
                    consecutive_fail += 1
                    time.sleep(1.5)
        return got_any

    def _listen_ws(self, room: dict) -> None:
        try:
            import websockets
        except ImportError:
            self._set(
                last_error="未安装 websockets，无法走推送通道",
                message="直连未成功，请改用模拟弹幕或检查房间号",
                connected=False,
            )
            return
        wss = _build_wss(room)
        headers = {
            "User-Agent": UA,
            "Origin": "https://live.douyin.com",
        }
        if room.get("ttwid"):
            headers["Cookie"] = f"ttwid={room['ttwid']}"

        async def _inner() -> None:
            try:
                async with websockets.connect(
                    wss,
                    additional_headers=headers,
                    max_size=2**23,
                    open_timeout=12,
                ) as ws:
                    self._set(connected=True, message="已连接（WebSocket 推送）", last_error="")
                    while not self._stop.is_set():
                        raw = await ws.recv()
                        if isinstance(raw, str):
                            raw = raw.encode("utf-8")
                        parsed = _parse_push_payload(raw)
                        if parsed["events"]:
                            self._emit(parsed["events"])
            except Exception as exc:
                self._set(
                    connected=False,
                    last_error=str(exc),
                    message="公开弹幕通道不可用（协议可能已变更），请用模拟弹幕或稍后重试",
                )

        import asyncio

        try:
            asyncio.run(_inner())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_inner())
            finally:
                loop.close()

    def _emit(self, events: list[ChatEvent]) -> None:
        for event in events:
            bus.publish(event)
            with self._lock:
                if event.event_type == "gift":
                    self._status.gift_count += 1
                else:
                    self._status.chat_count += 1


def _resolve_room(token: str) -> dict[str, str]:
    url = f"https://live.douyin.com/{token}"
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    with httpx.Client(timeout=15, headers=headers, follow_redirects=True) as client:
        resp = client.get(url)
        html = resp.text
        ttwid = resp.cookies.get("ttwid") or ""
    room_id = ""
    title = ""
    status = ""
    patterns = [
        r'\\"roomId\\":\\"(\d+)\\"',
        r'"roomId":"(\d+)"',
        r'"room_id":"(\d+)"',
        r'"room_id":(\d+)',
    ]
    for pat in patterns:
        found = re.search(pat, html)
        if found:
            room_id = found.group(1)
            break
    t_match = re.search(r'"title":"(.*?)"', html)
    if t_match:
        title = t_match.group(1)[:80]
    render = re.search(r'id="RENDER_DATA" type="application/json">(.*?)</script>', html)
    if render and not room_id:
        try:
            decoded = urllib.parse.unquote(render.group(1))
            data = json.loads(decoded)
            room_id = str(
                _dig(data, ["app", "initialState", "roomStore", "roomInfo", "roomId"]) or ""
            )
            title = str(
                _dig(data, ["app", "initialState", "roomStore", "roomInfo", "room", "title"])
                or title
            )
        except Exception:
            pass
    return {
        "web_rid": token,
        "room_id": room_id,
        "title": title,
        "status": status,
        "ttwid": ttwid,
    }


def _dig(data: Any, path: list[str]) -> Any:
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _build_wss(room: dict) -> str:
    ms = int(time.time() * 1000)
    did = str(ms)[-12:]
    query = {
        "app_name": "douyin_web",
        "version_code": "180800",
        "webcast_sdk_version": "1.0.14-beta.0",
        "update_version_code": "1.0.14-beta.0",
        "compress": "gzip",
        "device_platform": "web",
        "cookie_enabled": "true",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Mozilla",
        "browser_version": "5.0",
        "browser_online": "true",
        "tz_name": "Asia/Shanghai",
        "cursor": "d-1_u-1_h-1_t-1_r-1",
        "host": "https://live.douyin.com",
        "aid": "6383",
        "live_id": "1",
        "did_rule": "3",
        "endpoint": "live_pc",
        "support_wrds": "1",
        "im_path": "/webcast/im/fetch/",
        "identity": "audience",
        "need_persist_msg_count": "15",
        "room_id": room["room_id"],
        "user_unique_id": did,
        "heartbeatDuration": "0",
        "internal_ext": f"internal_src:dim|wss_push_room_id:{room['room_id']}|first_req_ms:{ms}",
    }
    return "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/?" + urllib.parse.urlencode(query)


def _maybe_gunzip(payload: bytes) -> bytes:
    if len(payload) >= 2 and payload[0] == 0x1F and payload[1] == 0x8B:
        try:
            return gzip.decompress(payload)
        except OSError:
            return payload
    return payload


def _parse_push_payload(raw: bytes) -> dict[str, Any]:
    events: list[ChatEvent] = []
    cursor = ""
    try:
        frame = decode_fields(_maybe_gunzip(raw))
        inner = first_bytes(frame, 8) or first_bytes(frame, 1) or raw
        body = decode_fields(_maybe_gunzip(inner))
        cursor = first_str(body, 2)
        messages = body.get(1) or []
        if not messages and first_bytes(frame, 8):
            messages = body.get(1) or []
        for item in messages:
            if not isinstance(item, (bytes, bytearray)):
                continue
            msg = decode_fields(bytes(item))
            method = first_str(msg, 1)
            payload = first_bytes(msg, 2) or b""
            payload = _maybe_gunzip(payload)
            if method == "WebcastChatMessage":
                ev = _parse_chat(payload)
                if ev:
                    events.append(ev)
            elif method == "WebcastGiftMessage":
                ev = _parse_gift(payload)
                if ev:
                    events.append(ev)
            elif method == "WebcastLikeMessage":
                ev = _parse_like(payload)
                if ev:
                    events.append(ev)
    except Exception:
        return {"events": [], "cursor": cursor}
    return {"events": events, "cursor": cursor}


def _parse_user(raw: bytes | None) -> tuple[str, str]:
    if not raw:
        return "", "观众"
    fields = decode_fields(raw)
    uid = str(first_int(fields, 1) or first_str(fields, 2) or "")
    nick = first_str(fields, 3) or first_str(fields, 2) or "观众"
    return uid, nick


def _parse_chat(payload: bytes) -> ChatEvent | None:
    fields = decode_fields(payload)
    uid, nick = _parse_user(first_bytes(fields, 2))
    content = first_str(fields, 3)
    if not content:
        return None
    return ChatEvent(nickname=nick, content=content, user_id=uid or nick, source="douyin")


def _parse_gift(payload: bytes) -> ChatEvent | None:
    fields = decode_fields(payload)
    uid, nick = _parse_user(first_bytes(fields, 2))
    gift = decode_fields(first_bytes(fields, 15) or first_bytes(fields, 6) or b"")
    name = first_str(gift, 2) or first_str(gift, 1) or "礼物"
    count = first_int(fields, 7) or first_int(fields, 5) or 1
    return ChatEvent(
        nickname=nick,
        content=name,
        user_id=uid or nick,
        source="douyin",
        event_type="gift",
        gift_name=name,
        gift_count=count or 1,
    )


def _parse_like(payload: bytes) -> ChatEvent | None:
    fields = decode_fields(payload)
    uid, nick = _parse_user(first_bytes(fields, 2))
    count = first_int(fields, 3) or 1
    return ChatEvent(
        nickname=nick,
        content="点赞",
        user_id=uid or nick,
        source="douyin",
        event_type="gift",
        gift_name="点赞",
        gift_count=count or 1,
    )


douyin_ingest = DouyinRoomIngest()
