from __future__ import annotations

import time
import uuid
from pathlib import Path

from app.config import api_key, load_config
from app.paths import TTS_DIR, ensure_user_dirs


def prepare_announcement(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"text": "", "audio_url": "", "use_browser": True}
    cfg = load_config()
    if not cfg.get("tts_enabled", True):
        return {"text": text, "audio_url": "", "use_browser": False}
    audio_url = ""
    if api_key():
        try:
            from app.siliconflow import synthesize_speech

            raw = synthesize_speech(text)
            ensure_user_dirs()
            name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.mp3"
            path = TTS_DIR / name
            path.write_bytes(raw)
            _cleanup()
            audio_url = f"/api/tts/{name}"
        except Exception:
            audio_url = ""
    return {
        "text": text,
        "audio_url": audio_url,
        "use_browser": not audio_url,
    }


def tts_file(name: str) -> Path | None:
    if "/" in name or "\\" in name or ".." in name:
        return None
    path = TTS_DIR / name
    if path.exists() and path.suffix == ".mp3":
        return path
    return None


def _cleanup(keep: int = 12) -> None:
    files = sorted(TTS_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
