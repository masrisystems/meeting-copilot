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
MIC_DEVICE = _env("MIC_DEVICE", "")  # substring of device name; empty = system default
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
# "auto": CUDA (large-v3-turbo, float16) when an NVIDIA GPU is usable, else CPU (small, int8)
WHISPER_DEVICE = _env("WHISPER_DEVICE", "auto")  # auto | cuda | cpu
WHISPER_MODEL = _env("WHISPER_MODEL", "auto")
WHISPER_COMPUTE = _env("WHISPER_COMPUTE", "auto")
WHISPER_DEFAULTS = {"cuda": ("large-v3-turbo", "float16"), "cpu": ("small", "int8")}
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE", "auto")  # auto (one of LANGUAGES, sticky per speaker) | any code
# Spoken languages (ISO 639-1, any of Whisper's ~99). Detection picks among these; the first is
# yours: translation goes into it (and from it into the second).
LANGUAGES = [code.strip() for code in _env("LANGUAGES", "de,en").split(",") if code.strip()]
# Force the reply language of the suggestions (e.g. "en"); empty = as the prompt says.
REPLY_LANGUAGE = _env("REPLY_LANGUAGE", "")
LANG_DETECT_EVERY = 4  # re-detect language every Nth chunk per source
PROMPT_CONTEXT_CHARS = 200

# --- Transcript ---
TURN_GAP_S = 1.2
MAX_TURNS = 15
ECHO_WINDOW_S = 3.0
ECHO_SIMILARITY = 80

# --- LLM ---
# Primary cloud engine (switchable live in the UI): gemini | claude | openai | grok | custom | ollama
LLM_PROVIDER = _env("LLM_PROVIDER", "gemini")
CLOUD_TIMEOUT_S = float(_env("CLOUD_TIMEOUT", _env("GEMINI_TIMEOUT", "2.5")))  # then Ollama races in parallel
CLOUD_HARD_TIMEOUT_S = 15  # a cloud call is abandoned (counted as an error) after this
BREAKER_FAILS = 2
BREAKER_COOLDOWN_S = 60

GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.6-flash")  # 3.7 is deprecated (routes to 3.8)
GEMINI_THINKING = _env("GEMINI_THINKING", "minimal")  # minimal | low | medium | high | off

ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-opus-5-5")  # faster: claude-sonnet-5-5, claude-haiku-5-5
CLAUDE_EFFORT = _env("CLAUDE_EFFORT", "low")  # low | medium | high | xhigh | max | off

OPENAI_API_KEY = _env("OPENAI_API_KEY", "")
OPENAI_BASE_URL = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-6-luna")

XAI_API_KEY = _env("XAI_API_KEY", "")
GROK_BASE_URL = _env("GROK_BASE_URL", "https://api.x.ai/v1")
GROK_MODEL = _env("GROK_MODEL", "grok-4.7")

# Any OpenAI-compatible endpoint: OmniRoute (http://localhost:20128/v1), OpenRouter, LiteLLM, LM Studio ...
CUSTOM_LABEL = _env("CUSTOM_LABEL", "Custom")
CUSTOM_BASE_URL = _env("CUSTOM_BASE_URL", "")
CUSTOM_API_KEY = _env("CUSTOM_API_KEY", "")  # optional for local gateways
CUSTOM_MODEL = _env("CUSTOM_MODEL", "")

OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "granite4:7b-a1b-h")
OLLAMA_TIMEOUT_S = 30

# --- Assistant ---
# React when the participant has finished a turn (this long without new speech), but during a
# monologue at the latest after PROACTIVE_MAX_PENDING_S.
PROACTIVE_DEBOUNCE_S = float(_env("PROACTIVE_PAUSE", "1.5"))
PROACTIVE_MAX_PENDING_S = float(_env("PROACTIVE_MAX_WAIT", "15"))
PROACTIVE_REPEAT_S = 60  # the same trigger again within this time is not shown again
SINGLE_SUGGESTION = _env("SINGLE_SUGGESTION", "false").lower() in ("1", "true", "yes")
SUGGESTION_READ_S = float(_env("SUGGESTION_READ_S", "10"))  # a new card waits this long before replacing one
MEMORY_INTERVAL_S = int(_env("MEMORY_INTERVAL", "300"))  # rolling meeting summary every 5 min
MEMORY_MIN_WORDS = 40  # skip the update when little was said

# --- Knowledge / facts ---
# Your own data (git-ignored). Starts empty; examples/ holds samples you can copy in.
FACTS_FILE = ROOT / "facts.json"
FLASHCARDS_FILE = ROOT / "knowledge_base.json"
KB_DIR = ROOT / "knowledge_base"
# Extra files or folders to index, separated by ";" (e.g. interview prep notes in a vault).
KB_EXTRA_PATHS = [Path(p.strip()) for p in _env("KB_EXTRA_PATHS", "").split(";") if p.strip()]
DB_DIR = ROOT / "db_py"
DB_TABLE = "docs"
# German/English. For other languages e.g. "intfloat/multilingual-e5-large" (re-indexes automatically).
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "jinaai/jina-embeddings-v2-base-de")
RAG_TOP_K = 3

# --- Paths ---
SESSIONS_DIR = ROOT / "sessions"
# One Markdown record per meeting (summary, facts, suggestions, transcript); e.g. a folder in your vault.
MEETINGS_DIR = Path(_env("MEETINGS_DIR", str(ROOT / "meetings"))).expanduser()
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
HOTKEY_MIC = "ctrl+alt+m"
HOTKEY_ON_TOP = "ctrl+alt+o"
