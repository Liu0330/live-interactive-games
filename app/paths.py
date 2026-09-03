from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
USER_DATA_DIR = ROOT / "data"
CONFIG_PATH = USER_DATA_DIR / "config.json"
DB_PATH = USER_DATA_DIR / "leaderboard.db"
TTS_DIR = USER_DATA_DIR / "tts"
WORDPOOL_PATH = DATA_DIR / "wordpool.txt"
QUESTIONS_PATH = DATA_DIR / "questions.json"
RELATED_PATH = DATA_DIR / "related_words.json"
COMMON_GUESSES_PATH = DATA_DIR / "common_guess_words.txt"


def ensure_user_dirs() -> None:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TTS_DIR.mkdir(parents=True, exist_ok=True)
