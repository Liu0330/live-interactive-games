from __future__ import annotations

import threading
import time
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db
from app.config import CHAT_MODELS, api_key, load_config, public_config, save_config
from app.games.manager import GAME_LABELS, manager
from app.games.quiz import load_questions, save_questions
from app.games.wordbank import add_words, load_words, remove_word, sanitize_generated, save_words
from app.ingest.douyin import douyin_ingest, extract_room_token
from app.ingest.mock import mock_ingest
from app.paths import STATIC_DIR, ensure_user_dirs
from app.tts import prepare_announcement, tts_file
from app.ws_hub import hub

app = FastAPI(title="直播互动玩法控制台", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_last_announce_seq = 0
_tick_started = False


class KeyBody(BaseModel):
    siliconflow_api_key: str = ""
    chat_model: str = ""


class GenerateBody(BaseModel):
    count: int = 50
    theme: str = ""
    overwrite: bool = False
    kind: str = "words"


class ControlBody(BaseModel):
    specified: str = ""


class SwitchBody(BaseModel):
    game: str


class ChatBody(BaseModel):
    nickname: str = "测试观众"
    content: str = ""


class GiftBody(BaseModel):
    nickname: str = "测试观众"
    gift_name: str = "小心心"
    count: int = 1


class RoomBody(BaseModel):
    room_id: str = ""


class WordsBody(BaseModel):
    words: list[str] = Field(default_factory=list)
    overwrite: bool = True


class WordBody(BaseModel):
    word: str


class ConfigBody(BaseModel):
    payload: dict = Field(default_factory=dict)


def _html(name: str) -> HTMLResponse:
    path = STATIC_DIR / name
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.on_event("startup")
def _startup() -> None:
    global _tick_started
    ensure_user_dirs()
    db.init_db()
    if not _tick_started:
        _tick_started = True
        threading.Thread(target=_tick_loop, daemon=True).start()


def _tick_loop() -> None:
    global _last_announce_seq
    while True:
        time.sleep(1)
        try:
            notes = manager.tick()
            payload = manager.snapshot(host=True)
            if "announce" in notes or manager.announce_seq != _last_announce_seq:
                _last_announce_seq = manager.announce_seq
                payload["tts"] = prepare_announcement(manager.last_announce)
            _broadcast(payload)
        except Exception:
            continue


def _broadcast(payload: dict | None = None) -> None:
    data = payload or manager.snapshot(host=True)
    try:
        loop = None
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(hub.broadcast({"type": "state", **data}))
        else:
            # 从同步线程唤醒
            _schedule_broadcast(data)
    except Exception:
        pass


_main_loop = None


def _schedule_broadcast(data: dict) -> None:
    loop = _main_loop
    if loop is None:
        return
    asyncio_run = __import__("asyncio")
    asyncio_run.run_coroutine_threadsafe(hub.broadcast({"type": "state", **data}), loop)


@app.on_event("startup")
async def _capture_loop() -> None:
    global _main_loop
    import asyncio

    _main_loop = asyncio.get_running_loop()


@app.get("/")
def root() -> HTMLResponse:
    return _html("index.html")


@app.get("/control")
def control_page() -> HTMLResponse:
    return _html("control.html")


@app.get("/overlay")
def overlay_page() -> HTMLResponse:
    return _html("overlay.html")


@app.get("/api/state")
def api_state(role: str = "overlay") -> dict:
    return manager.snapshot(host=(role == "control"))


@app.get("/api/config")
def api_config() -> dict:
    data = public_config()
    data["ingest"] = douyin_ingest.status().as_dict()
    data["games"] = GAME_LABELS
    data["word_count"] = len(load_words())
    data["question_count"] = len(load_questions())
    data["chat_models"] = CHAT_MODELS
    return data


@app.post("/api/config")
def api_save_config(body: ConfigBody) -> dict:
    allowed = {
        "chat_model",
        "embed_model",
        "tts_enabled",
        "tts_model",
        "tts_voice",
        "douyin_room_id",
        "points_per_sublevel",
        "rank_names",
        "semantic",
        "quiz",
        "bomb",
        "lottery",
        "gifts",
        "active_game",
    }
    patch = {k: v for k, v in (body.payload or {}).items() if k in allowed}
    save_config(patch)
    return {"ok": True, "config": public_config()}


@app.post("/api/key")
def api_save_key(body: KeyBody) -> dict:
    patch = {}
    if body.siliconflow_api_key.strip():
        patch["siliconflow_api_key"] = body.siliconflow_api_key.strip()
    if body.chat_model.strip():
        patch["chat_model"] = body.chat_model.strip()
    save_config(patch)
    return {"ok": True, "config": public_config()}


@app.post("/api/key/test")
def api_test_key() -> dict:
    from app.siliconflow import SiliconFlowError, test_connection

    if not api_key():
        raise HTTPException(400, "请先保存 API Key")
    try:
        return test_connection()
    except SiliconFlowError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/generate")
def api_generate(body: GenerateBody) -> dict:
    from app.siliconflow import SiliconFlowError, generate_questions, generate_words

    try:
        if body.kind == "questions":
            items = generate_questions(body.count, body.theme)
            bank = save_questions(items, overwrite=body.overwrite)
            return {"ok": True, "count": len(bank), "added": len(items)}
        words = sanitize_generated(generate_words(body.count, body.theme))
        bank = add_words(words, overwrite=body.overwrite)
        return {"ok": True, "count": len(bank), "added": len(words), "words": words}
    except SiliconFlowError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/words")
def api_words() -> dict:
    words = load_words()
    return {"words": words, "count": len(words)}


@app.post("/api/words")
def api_set_words(body: WordsBody) -> dict:
    words = save_words(body.words) if body.overwrite else add_words(body.words)
    return {"ok": True, "count": len(words), "words": words}


@app.post("/api/words/delete")
def api_del_word(body: WordBody) -> dict:
    words = remove_word(body.word.strip())
    return {"ok": True, "count": len(words), "words": words}


@app.post("/api/game/switch")
def api_switch(body: SwitchBody) -> dict:
    manager.switch(body.game)
    _broadcast()
    return {"ok": True, "game": manager.active_id}


@app.post("/api/round/start")
def api_start(body: ControlBody) -> dict:
    manager.start_round(body.specified)
    payload = manager.snapshot(host=True)
    payload["tts"] = prepare_announcement(manager.last_announce)
    _broadcast(payload)
    return {"ok": True, "state": payload}


@app.post("/api/round/skip")
def api_skip() -> dict:
    manager.skip()
    payload = manager.snapshot(host=True)
    payload["tts"] = prepare_announcement(manager.last_announce)
    _broadcast(payload)
    return {"ok": True, "state": payload}


@app.post("/api/leaderboard/clear")
def api_clear_board() -> dict:
    db.clear_leaderboard()
    _broadcast()
    return {"ok": True}


@app.post("/api/mock/chat")
def api_mock_chat(body: ChatBody) -> dict:
    if not body.content.strip():
        raise HTTPException(400, "请填写猜词内容")
    mock_ingest.inject_chat(body.nickname, body.content)
    payload = manager.snapshot(host=True)
    if manager.announce_seq:
        payload["tts"] = prepare_announcement(manager.last_announce)
    _broadcast(payload)
    return {"ok": True, "state": payload}


@app.post("/api/mock/gift")
def api_mock_gift(body: GiftBody) -> dict:
    mock_ingest.inject_gift(body.nickname, body.gift_name, body.count)
    payload = manager.snapshot(host=True)
    payload["tts"] = prepare_announcement(manager.last_announce)
    _broadcast(payload)
    return {"ok": True, "state": payload}


@app.post("/api/douyin/connect")
def api_douyin_connect(body: RoomBody) -> dict:
    token = extract_room_token(body.room_id or load_config().get("douyin_room_id") or "")
    if not token:
        raise HTTPException(400, "请填写直播间数字房间号")
    save_config({"douyin_room_id": token})
    try:
        douyin_ingest.start(token)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "ingest": douyin_ingest.status().as_dict()}


@app.post("/api/douyin/disconnect")
def api_douyin_disconnect() -> dict:
    douyin_ingest.stop()
    return {"ok": True, "ingest": douyin_ingest.status().as_dict()}


@app.get("/api/douyin/status")
def api_douyin_status() -> dict:
    return douyin_ingest.status().as_dict()


@app.get("/api/tts/{name}")
def api_tts(name: str) -> FileResponse:
    path = tts_file(name)
    if not path:
        raise HTTPException(404, "音频不存在")
    return FileResponse(path, media_type="audio/mpeg")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json({"type": "state", **manager.snapshot(host=True)})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:
        await hub.disconnect(ws)


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
