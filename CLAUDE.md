# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A real-time meeting copilot in Python (`copilot/`) for Windows, Linux and macOS. It captures the mic and the system audio, does VAD chunking, transcribes with faster-whisper (CUDA, or CPU when no NVIDIA GPU is usable), and calls a cloud AI with an Ollama fallback. The UI is a local web page. See README.md for setup, hotkeys and the pipeline overview, and `tickets/` for the roadmap (Echo ticket format, `tickets/backlog/NNN_*.md`; commits reference `(#NNN)`).

The project is open source (MIT) at `github.com/masrisystems/meeting-copilot` (remote `public`). The private repo `masrisystems/meetings` (remote `origin`) has the same `main` plus the `legacy` branch, which holds the old Meetily-based Node scripts and private knowledge files. Never commit `knowledge_base/`, `knowledge_base.json`, `facts.json`, `.env` or `sessions/`: they are the user's private data and are git-ignored. Public samples live in `examples/`.

## Commands

```sh
uv sync                                        # Python 3.12 env (pinned; 3.13+ lacks reliable wheels)
uv run python -m copilot [--no-browser --no-hotkeys --fresh-facts]
uv run pytest                                  # all unit tests (no audio/GPU), CI: .github/workflows/ci.yml
uv run pytest tests/test_core.py::test_echo_mic_before_loopback_is_retracted   # single test
uv run python -m copilot.stt_bench file.wav    # real VAD + Whisper latency on a WAV
uv run python -m copilot.devices               # device list + live levels
uv run python -m copilot.rag index | search "<query>"
```

## Architecture notes (non-obvious parts)

- **Threads vs loop:** each audio source has a `VadWorker` thread, and one `SttWorker` thread owns the Whisper model. Everything else (assistant, memory loop, LLM, FastAPI/uvicorn, WebSocket) runs on one asyncio loop. Segments cross over via `loop.call_soon_threadsafe(assistant.on_segment, seg)`. `Hub.publish` must only be called on the loop.
- **Platforms** (`audio.py`): one `_Source` interface (`device["name"]`, `queue`, `level`, `start/stop`) with three backends. Windows: `pyaudiowpatch` WASAPI mic + loopback. Linux: one `parec` subprocess per source, `@DEFAULT_SOURCE@` / `@DEFAULT_MONITOR@`, PulseAudio resamples to 16 kHz mono float32. macOS: `sounddevice`; the loopback is a virtual device (BlackHole), since CoreAudio has none. Platform packages are gated with `sys_platform` markers in `pyproject.toml`; import them only inside the matching branch. Hotkeys: `keyboard` on Windows, `pynput` elsewhere (X11 only on Linux); failures only print a hint. CI runs tests and import smoke checks on all three OSes.
- **Whisper settings** (`stt.whisper_settings`): `WHISPER_DEVICE/MODEL/COMPUTE=auto` resolve via `ctranslate2.get_cuda_device_count()` to `large-v3-turbo`/`float16` on CUDA or `small`/`int8` on CPU.
- **CUDA DLLs:** `copilot/cuda.py` must be imported before faster_whisper/ctranslate2. On Windows it registers the pip-installed `nvidia/*/bin` dirs (no-op elsewhere; Linux uses system CUDA libraries). `stt.py` does this.
- **Silero VAD** uses the ONNX file bundled with faster-whisper (`faster_whisper/assets/silero_vad_v6.onnx`), run frame by frame. The v6 model needs the previous frame's last 64 samples prepended (input `(1, 576)`). `Chunker` is pure logic and unit-tested; `SileroStream` is stateful.
- **WASAPI loopback sends no data while output is silent.** `VadWorker` feeds synthetic silence on queue timeouts so open utterances still end.
- **Whisper latency:** auto language detection costs a full extra encoder pass (~240 ms), so the language is sticky per source and re-detected every `LANG_DETECT_EVERY` chunks. `temperature=0.0` disables the fallback re-decodes. Models load with `local_files_only=True` first, because the HF freshness check can take minutes.
- **Echo handling** (`transcript.py`): a mic segment that fuzzy-matches a recent loopback segment is dropped. If the mic copy arrived first, it is retracted (removed from `turns` and `segments`), and the UI receives a `retract` event.
- **Context sent to the LLM** (`Assistant._context`): `[FACTS]` + `[MEMORY]` + `[KNOWLEDGE]` + `[TRANSCRIPT]` (last `MAX_TURNS` turns). `Transcript.turns` is a short deque; `Transcript.segments` keeps the whole meeting for `memory.py`, which folds segments newer than its id cursor into a rolling summary every `MEMORY_INTERVAL_S`.
- **Prompts:** `PROMPT` in `.env` selects `copilot/prompts/<name>.md` (`system` for sales calls, `interview`). Every prompt must keep the same JSON output format as `SUGGESTION_SCHEMA` in `llm.py`.
- **Providers** (`providers.py`): one interface, `complete(system, prompt, schema) -> str`, implemented by `GeminiProvider`, `ClaudeProvider` (anthropic SDK, `output_config` json_schema + effort, server-side refusal `fallbacks: "default"` on Opus/Sonnet 5.x, never send `temperature`), `OpenAICompatProvider` (openai SDK with `base_url`; used for `openai`, `grok`, `custom`/OmniRoute; JSON mode downgrades schema -> object -> prompt when an endpoint rejects `response_format`), and `OllamaProvider`. All SDKs run with `max_retries=0`, because `llm.py` owns failover. `retry_after()` maps 429s (header or Gemini `retryDelay`) to a pause.
- **LLM** (`llm.py`): the primary is selected by `LLM_PROVIDER` and switched live from the UI (`set_primary`, a `providers` event). The call is hedged: after `CLOUD_TIMEOUT_S` Ollama starts in parallel and the first success wins. Only errors trip the breaker, not slowness. `llm.note` explains to the UI why the cloud is off. `SUGGESTION_SCHEMA` is closed (`additionalProperties: false`) for Claude and OpenAI strict mode. Gemini's free tier is about 20 requests/day/model, which is unusable for meetings.
- **Proactive scheduling** (`assistant.py`): fires at the end of a participant turn (debounce `PROACTIVE_DEBOUNCE_S`), capped at `PROACTIVE_MAX_PENDING_S` for monologues. It is single-flight with one trailing re-run, so at most one proactive LLM call is in flight. A trigger equal to the last one within `PROACTIVE_REPEAT_S` is dropped. The UI (`index.html`) holds the top card for `read_s` (sent in the `ui` event), while `user_speaking` (mic VAD state in `levels`) and while a card is pinned; newer cards wait in a one-slot queue. History replays carry `replay: true` and bypass the queue.
- **Knowledge paths** (`config.py`): `knowledge_base/` and `knowledge_base.json` start empty (no fallback to `examples/`, which only holds samples). `KB_EXTRA_PATHS` adds files or folders. The index lives in `db_py/`; `rag.sync()` re-indexes only when the file signature in `db_py/sources.json` changed, and `rag.watch()` runs it at startup and on every change. An empty source set drops the table.
- **Meetings** (`assistant.py`): the app starts idle. `start_meeting()` creates a new session (transcript, memory) and opens the devices through `open_capture` (the `Capture` is built lazily on the first start, so device errors become a `meeting` event with `error` instead of a crash). Sources are opened and closed on start/stop/mute, so `_Source.start()/stop()` must be idempotent and restartable on every backend.
- **Ollama models** (`llm.py`): `check_ollama()` polls `/api/tags` every 30 s; `describe()` carries `ollama_models` and `ollama_note`. The UI lists each model as `ollama:<model>`, and `set_primary("ollama:<model>")` switches the model of the one Ollama provider (primary and fallback).
- **State files:** running the app writes to the real `facts.json` and `sessions/`, so tests use `tmp_path`.
- **Install / launchers:** `install.sh` (Linux .desktop, macOS .app bundle) and `install.ps1` (Windows .lnk via pythonw) start `.venv` python with `--exit-on-close --log sessions/copilot.log`. The icon master is `copilot/web/icon.svg` (also the favicon); `packaging/icon.png` (1024) and `icon.ico` are rendered from it.
- **Meeting records** (`export.py`): `stop_meeting()` returns a task that summarizes the last minutes and writes `MEETINGS_DIR/Meeting <date> <time>.md`; it works on the old meeting's objects because a new meeting may already run. `__main__` awaits it on shutdown during a meeting. Suggestions and answers go through `Assistant._card` so they are recorded.
- **Window** (`window.py`): always-on-top and opacity for the Chrome `--app` window, found by its exact title "Meeting Copilot" (Win32 API on Windows, EWMH client messages/properties via python-xlib on X11; unsupported on Wayland/macOS). Applied whenever a page connects (with retries) and persisted in `settings.json`.
- **Languages:** `LANGUAGES` (first = the user's) restricts Whisper's detection and sets the translation target; `REPLY_LANGUAGE` appends a language rule to the system prompt; `EMBEDDING_MODEL` is part of the index signature, so changing it re-indexes.
- UI rule from the user's global instructions: no emojis; use inline SVG icons.
