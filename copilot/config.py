"""All tunables in one place. Secrets and per-machine overrides come from .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name, default):
    return os.environ.get(name) or default


# --- Audio ---
SAMPLE_RATE = 16000
MIC_DEVICE = _env("MIC_DEVICE", "")  # substring of device name; empty = Windows default
LOOPBACK_DEVICE = _env("LOOPBACK_DEVICE", "")  # substring of output device name; empty = default

# --- VAD / chunking ---
VAD_FRAME = 512  # samples (32 ms at 16 kHz), fixed by Silero
VAD_THRESHOLD = 0.5
CHUNK_MIN_S = 1.5
CHUNK_MAX_S = 2.5
SILENCE_END_S = 0.3
PRE_ROLL_S = 0.2
MIN_UTTERANCE_S = 0.25  # anything shorter is a click/cough, dropped

# --- STT ---
WHISPER_MODEL = _env("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = _env("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE = _env("WHISPER_COMPUTE", "float16")
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE", "auto")  # auto (de/en, sticky per speaker) | de | en
LANG_DETECT_EVERY = 4  # re-detect language every Nth chunk per source
PROMPT_CONTEXT_CHARS = 200

# --- Transcript ---
TURN_GAP_S = 1.2
MAX_TURNS = 15
ECHO_WINDOW_S = 3.0
ECHO_SIMILARITY = 80

# --- LLM ---
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.6-flash")  # 3.7 is deprecated (routes to 3.8)
GEMINI_THINKING = _env("GEMINI_THINKING", "minimal")  # minimal | low | medium | high | off
GEMINI_TIMEOUT_S = float(_env("GEMINI_TIMEOUT", "2.5"))  # after this, Ollama races in parallel
GEMINI_HARD_TIMEOUT_S = 15  # Gemini call is abandoned (counted as an error) after this
GEMINI_BREAKER_FAILS = 2
GEMINI_BREAKER_COOLDOWN_S = 60
OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "granite4:7b-a1b-h")
OLLAMA_TIMEOUT_S = 30

# --- Assistant ---
PROACTIVE_DEBOUNCE_S = 1.5
PROACTIVE_MAX_PENDING_S = 2.5
MEMORY_INTERVAL_S = int(_env("MEMORY_INTERVAL", "300"))  # rolling meeting summary every 5 min
MEMORY_MIN_WORDS = 40  # skip the update when little was said

# --- Knowledge / facts ---
def _existing_or_example(name):
    """Own data (git-ignored) if present, otherwise the bundled example."""
    own = ROOT / name
    return own if own.exists() else ROOT / "examples" / name


FACTS_FILE = ROOT / "facts.json"
FLASHCARDS_FILE = _existing_or_example("knowledge_base.json")
KB_DIR = _existing_or_example("knowledge_base")
# Extra files or folders to index, separated by ";" (e.g. interview prep notes in a vault).
KB_EXTRA_PATHS = [Path(p.strip()) for p in _env("KB_EXTRA_PATHS", "").split(";") if p.strip()]
DB_DIR = ROOT / "db_py"
DB_TABLE = "docs"
EMBEDDING_MODEL = "jinaai/jina-embeddings-v2-base-de"
RAG_TOP_K = 3

# --- Paths ---
SESSIONS_DIR = ROOT / "sessions"
# prompts/<PROMPT>.md: "system" (client/sales calls) or "interview" (job interviews, meetings at work)
SYSTEM_PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / (_env("PROMPT", "system") + ".md")
WEB_DIR = Path(__file__).resolve().parent / "web"

# --- Server / UI ---
HOST = "127.0.0.1"
PORT = int(_env("PORT", "8765"))
WINDOW_SIZE = (520, 900)

# --- Hotkeys ---
# Shown as buttons above the question box and bound to the hotkeys.
PRESETS = [
    {"hotkey": "ctrl+alt+1", "label": "Was antworten?", "query": "Was soll ich jetzt antworten?"},
    {"hotkey": "ctrl+alt+2", "label": "Schwächen", "query": "Zeig die Schwächen in ihrem Angebot bzw. Argument auf."},
    {"hotkey": "ctrl+alt+3", "label": "Zusammenfassung", "query": "Fasse die letzten Minuten in 2 Sätzen zusammen."},
]
HOTKEY_FOCUS = "ctrl+alt+space"
HOTKEY_TRANSLATE = "ctrl+alt+t"
HOTKEY_PROACTIVE = "ctrl+alt+p"
