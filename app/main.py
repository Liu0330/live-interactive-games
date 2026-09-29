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
from app.games.quiz import load_questions
from app.engagement import sanitize_gift_tiers, sanitize_likes, sanitize_streak
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


class LlmBody(BaseModel):
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_related_can_win: bool = False


class MinimaxBody(BaseModel):
    minimax_api_key: str = ""
    minimax_base_url: str = ""
    minimax_chat_model: str = ""
    minimax_embed_model: str = ""
    minimax_tts_model: str = ""
    minimax_tts_voice: str = ""
    llm_related_can_win: bool = False


class GenerateBody(BaseModel):
    count: int = 50
    theme: str = ""
    overwrite: bool = False
    kind: str = "words"


class ControlBody(BaseModel):
    specified: str = ""


class SwitchBody(BaseModel):
    game: str


class PauseBody(BaseModel):
    paused: bool = True


class ChatBody(BaseModel):
    nickname: str = "测试观众"
    content: str = ""


class GiftBody(BaseModel):
    nickname: str = "测试观众"
    gift_name: str = "小心心"
    count: int = 1
    gift_value: int = 0


class LikeBody(BaseModel):
    nickname: str = "测试观众"
    count: int = 10


class MemberBody(BaseModel):
    nickname: str = "新观众"


class RoomBody(BaseModel):
    room_id: str = ""


class WordsBody(BaseModel):
    words: list[str] = Field(default_factory=list)
    overwrite: bool = True


class WordBody(BaseModel):
    word: str


class ConfigBody(BaseModel):
    payload: dict = Field(default_factory=dict)


class EmojiGenerateBody(BaseModel):
    count: int = 4
    category: str = "idiom"


class EmojiDeleteBody(BaseModel):
    puzzle_id: str = ""


class BankTextBody(BaseModel):
    kind: str = "word"
    text: str = ""
    category: str = ""


class BankGenerateBody(BaseModel):
    kind: str = "word"
    category: str = ""
    theme: str = ""
    count: int = 8
    auto_add: bool = False


class BankSaveBody(BaseModel):
    kind: str = "word"
    items: list[dict] = Field(default_factory=list)


class BankUpdateBody(BaseModel):
    item_id: str = ""
    category: str = ""
    label: str = ""
    extra: str = ""
    text: str = ""


class BankDeleteBody(BaseModel):
    item_id: str = ""


class BankReviewBody(BaseModel):
    suggestion_id: int = 0
    action: str = "approve"


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
            payload = _with_tts(manager.snapshot(host=True), force="announce" in notes)
            _broadcast(payload)
        except Exception:
            continue


def _with_tts(payload: dict, force: bool = False) -> dict:
    global _last_announce_seq
    if force or manager.announce_seq != _last_announce_seq:
        _last_announce_seq = manager.announce_seq
        if manager.last_announce:
            payload["tts"] = prepare_announcement(manager.last_announce)
    return payload


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


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


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
    from app.banks import playable_words

    data["word_count"] = len(playable_words())
    data["question_count"] = len(load_questions())
    data["chat_models"] = CHAT_MODELS
    return data


@app.post("/api/config")
def api_save_config(body: ConfigBody) -> dict:
    allowed = {
        "chat_model",
        "llm_base_url",
        "llm_model",
        "llm_related_can_win",
        "llm_related_count",
        "minimax_base_url",
        "minimax_chat_model",
        "minimax_embed_model",
        "minimax_tts_model",
        "minimax_tts_voice",
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
        "idiom",
        "emoji",
        "gifts",
        "gift_tiers",
        "likes",
        "streak",
        "active_game",
        "auto_continue",
        "intermission_seconds",
        "count_intermission_chat",
        "danmaku",
    }
    patch = {k: v for k, v in (body.payload or {}).items() if k in allowed}
    if "gift_tiers" in patch:
        patch["gift_tiers"] = sanitize_gift_tiers(patch["gift_tiers"])
    if "likes" in patch:
        patch["likes"] = sanitize_likes(patch["likes"])
    if "streak" in patch:
        patch["streak"] = sanitize_streak(patch["streak"])
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


@app.post("/api/llm")
def api_save_llm(body: LlmBody) -> dict:
    patch: dict = {
        "llm_base_url": body.llm_base_url.strip(),
        "llm_model": body.llm_model.strip() or "glm-5.2",
        "llm_related_can_win": bool(body.llm_related_can_win),
    }
    if body.llm_api_key.strip():
        patch["llm_api_key"] = body.llm_api_key.strip()
    save_config(patch)
    return {"ok": True, "config": public_config()}


@app.post("/api/minimax")
def api_save_minimax(body: MinimaxBody) -> dict:
    patch: dict = {
        "minimax_base_url": body.minimax_base_url.strip() or "https://api.minimaxi.com",
        "minimax_chat_model": body.minimax_chat_model.strip() or "MiniMax-M3",
        "minimax_embed_model": body.minimax_embed_model.strip() or "embo-01",
        "minimax_tts_model": body.minimax_tts_model.strip() or "speech-02-turbo",
        "minimax_tts_voice": body.minimax_tts_voice.strip() or "male-qn-qingse",
        "llm_related_can_win": bool(body.llm_related_can_win),
    }
    if body.minimax_api_key.strip():
        patch["minimax_api_key"] = body.minimax_api_key.strip()
    save_config(patch)
    return {"ok": True, "config": public_config()}


@app.post("/api/minimax/test")
def api_test_minimax() -> dict:
    from app.config import minimax_ready
    from app.minimax import MinimaxError, test_connection

    if not minimax_ready():
        raise HTTPException(400, "请先保存 MiniMax 密钥")
    try:
        return test_connection()
    except MinimaxError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/llm/test")
def api_test_llm() -> dict:
    from app.config import llm_ready
    from app.llm import ChatError, test_connection

    if not llm_ready():
        raise HTTPException(400, "请先保存对话接口地址和密钥")
    try:
        return test_connection()
    except ChatError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/generate")
def api_generate(body: GenerateBody) -> dict:
    from app.banks import draft, save_drafts
    from app.llm import ChatError
    from app.minimax import MinimaxError

    kind = "question" if body.kind == "questions" else "word"
    try:
        rows = draft(kind, body.theme, body.theme, body.count)
        saved = save_drafts(kind, rows)
    except (ChatError, MinimaxError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "count": len(saved["added"]), "added": len(saved["added"]), "words": [item.get("word") or item.get("question") for item in saved["added"]]}


@app.get("/api/words")
def api_words() -> dict:
    from app.banks import playable_words

    words = playable_words()
    return {"words": words, "count": len(words)}


@app.post("/api/words")
def api_set_words(body: WordsBody) -> dict:
    from app.banks import add_text, playable_words

    if body.overwrite:
        raise HTTPException(400, "为避免清掉已有词库，请逐条删除，不要整库覆盖")
    add_text("word", "\n".join(body.words), source="manual")
    words = playable_words()
    return {"ok": True, "count": len(words), "words": words}


@app.post("/api/words/delete")
def api_del_word(body: WordBody) -> dict:
    from app.banks import playable_words, remove_item

    word = body.word.strip()
    remove_item(f"file:word:{word}")
    words = playable_words()
    return {"ok": True, "count": len(words), "words": words}


@app.get("/api/bank")
def api_bank(kind: str = "word", q: str = "", category: str = "") -> dict:
    from app.banks import catalog

    try:
        return catalog(kind, q, category)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/bank/items")
def api_bank_items(body: BankTextBody) -> dict:
    from app.banks import add_text

    try:
        result = add_text(body.kind, body.text, body.category, source="manual")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **result}


@app.post("/api/bank/generate")
def api_bank_generate(body: BankGenerateBody) -> dict:
    from app.banks import draft, save_drafts
    from app.llm import ChatError
    from app.minimax import MinimaxError

    try:
        rows = draft(body.kind, body.category, body.theme, body.count)
        saved = save_drafts(body.kind, rows) if body.auto_add else {"added": [], "skipped": [], "rejected": []}
    except (ChatError, MinimaxError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "drafts": rows, "added": saved["added"]}


@app.post("/api/bank/save")
def api_bank_save(body: BankSaveBody) -> dict:
    from app.banks import save_drafts

    try:
        result = save_drafts(body.kind, body.items, source="ai")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **result}


@app.post("/api/bank/update")
def api_bank_update(body: BankUpdateBody) -> dict:
    from app.banks import update_item

    try:
        item = update_item(body.item_id, body.category, body.label, body.extra, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "item": item}


@app.post("/api/bank/delete")
def api_bank_delete(body: BankDeleteBody) -> dict:
    from app.banks import remove_item

    remove_item(body.item_id)
    return {"ok": True}


@app.post("/api/bank/import")
def api_bank_import(body: BankTextBody) -> dict:
    from app.banks import add_text

    try:
        result = add_text(body.kind, body.text, body.category, source="import")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **result}


@app.get("/api/bank/export")
def api_bank_export(kind: str = "word", fmt: str = "txt"):
    from fastapi.responses import Response

    from app.banks import export_text

    try:
        text, filename, media = export_text(kind, "csv" if fmt == "csv" else "txt")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(content=text, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/bank/suggestions")
def api_bank_suggestions() -> dict:
    from app.banks import list_suggestions

    return {"suggestions": list_suggestions()}


@app.post("/api/bank/suggestions/review")
def api_bank_review(body: BankReviewBody) -> dict:
    from app.banks import review_suggestion

    try:
        return review_suggestion(body.suggestion_id, body.action)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/emoji/puzzles")
def api_emoji_puzzles() -> dict:
    from app.games.emoji_bank import all_puzzles

    puzzles = all_puzzles()
    return {"puzzles": puzzles, "count": len(puzzles)}


@app.post("/api/emoji/generate")
def api_emoji_generate(body: EmojiGenerateBody) -> dict:
    from app.games.emoji_bank import generate_with_model
    from app.llm import ChatError
    from app.minimax import MinimaxError

    try:
        saved = generate_with_model(body.count, body.category)
    except (ChatError, MinimaxError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "added": len(saved), "puzzles": saved}


@app.post("/api/emoji/delete")
def api_emoji_delete(body: EmojiDeleteBody) -> dict:
    from app.games.emoji_bank import all_puzzles, delete_puzzle

    delete_puzzle(body.puzzle_id)
    puzzles = all_puzzles()
    return {"ok": True, "count": len(puzzles), "puzzles": puzzles}


@app.post("/api/pause")
def api_pause(body: PauseBody) -> dict:
    if body.paused:
        manager.pause()
    else:
        manager.resume()
    payload = _with_tts(manager.snapshot(host=True))
    _broadcast(payload)
    return {"ok": True, "paused": manager.paused, "state": payload}


@app.post("/api/game/switch")
def api_switch(body: SwitchBody) -> dict:
    manager.switch(body.game)
    _broadcast()
    return {"ok": True, "game": manager.active_id}


@app.post("/api/round/start")
def api_start(body: ControlBody) -> dict:
    manager.start_round(body.specified)
    payload = _with_tts(manager.snapshot(host=True), force=True)
    _broadcast(payload)
    return {"ok": True, "state": payload}


@app.post("/api/round/skip")
def api_skip() -> dict:
    manager.skip()
    payload = _with_tts(manager.snapshot(host=True), force=True)
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
    payload = _with_tts(manager.snapshot(host=True))
    _broadcast(payload)
    return {"ok": True, "state": payload}


@app.post("/api/mock/gift")
def api_mock_gift(body: GiftBody) -> dict:
    mock_ingest.inject_gift(body.nickname, body.gift_name, body.count, body.gift_value)
    payload = _with_tts(manager.snapshot(host=True), force=True)
    _broadcast(payload)
    return {"ok": True, "state": payload}


@app.post("/api/mock/like")
def api_mock_like(body: LikeBody) -> dict:
    mock_ingest.inject_like(body.nickname, body.count)
    payload = _with_tts(manager.snapshot(host=True))
    _broadcast(payload)
    return {"ok": True, "state": payload}


@app.post("/api/mock/member")
def api_mock_member(body: MemberBody) -> dict:
    mock_ingest.inject_member(body.nickname)
    payload = _with_tts(manager.snapshot(host=True), force=True)
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
