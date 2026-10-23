# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows real-time meeting copilot in Python (`copilot/`). It captures the mic and WASAPI loopback, does VAD chunking, transcribes with faster-whisper on CUDA, and calls Gemini with an Ollama fallback. The UI is a local web page. See README.md for setup, hotkeys and the pipeline overview, and `tickets/` for the roadmap (Echo ticket format, `tickets/backlog/NNN_*.md`; commits reference `(#NNN)`).

The project is open source (MIT) at `github.com/masrisystems/meeting-copilot` (remote `public`). The private repo `masrisystems/meetings` (remote `origin`) has the same `main` plus the `legacy` branch, which holds the old Meetily-based Node scripts and private knowledge files. Never commit `knowledge_base/`, `knowledge_base.json`, `facts.json`, `.env` or `sessions/`: they are the user's private data and are git-ignored. Public samples live in `examples/`.

## Commands

```sh
uv sync                                        # Python 3.12 env (pinned; 3.13+ lacks reliable wheels)
uv run python -m copilot [--no-browser --no-hotkeys --fresh-facts]
uv run pytest                                  # all unit tests (no audio/GPU)
uv run pytest tests/test_core.py::test_echo_mic_before_loopback_is_retracted   # single test
uv run python -m copilot.stt_bench file.wav    # real VAD + Whisper latency on a WAV
uv run python -m copilot.devices               # device list + live levels
uv run python -m copilot.rag index | search "<query>"
```

## Architecture notes (non-obvious parts)

- **Threads vs loop:** each audio source has a `VadWorker` thread, and one `SttWorker` thread owns the Whisper model. Everything else (assistant, memory loop, LLM, FastAPI/uvicorn, WebSocket) runs on one asyncio loop. Segments cross over via `loop.call_soon_threadsafe(assistant.on_segment, seg)`. `Hub.publish` must only be called on the loop.
- **CUDA DLLs:** `copilot/cuda.py` must be imported before faster_whisper/ctranslate2. It registers the pip-installed `nvidia/*/bin` dirs. `stt.py` does this.
- **Silero VAD** uses the ONNX file bundled with faster-whisper (`faster_whisper/assets/silero_vad_v6.onnx`), run frame by frame. The v6 model needs the previous frame's last 64 samples prepended (input `(1, 576)`). `Chunker` is pure logic and unit-tested; `SileroStream` is stateful.
- **WASAPI loopback sends no data while output is silent.** `VadWorker` feeds synthetic silence on queue timeouts so open utterances still end.
- **Whisper latency:** auto language detection costs a full extra encoder pass (~240 ms), so the language is sticky per source and re-detected every `LANG_DETECT_EVERY` chunks. `temperature=0.0` disables the fallback re-decodes. Models load with `local_files_only=True` first, because the HF freshness check can take minutes.
- **Echo handling** (`transcript.py`): a mic segment that fuzzy-matches a recent loopback segment is dropped. If the mic copy arrived first, it is retracted (removed from `turns` and `segments`), and the UI receives a `retract` event.
- **Context sent to the LLM** (`Assistant._context`): `[FACTS]` + `[MEMORY]` + `[KNOWLEDGE]` + `[TRANSCRIPT]` (last `MAX_TURNS` turns). `Transcript.turns` is a short deque; `Transcript.segments` keeps the whole meeting for `memory.py`, which folds segments newer than its id cursor into a rolling summary every `MEMORY_INTERVAL_S`.
- **Prompts:** `PROMPT` in `.env` selects `copilot/prompts/<name>.md` (`system` for sales calls, `interview`). Every prompt must keep the same JSON output format as `SUGGESTION_SCHEMA` in `llm.py`.
- **Providers** (`providers.py`): one interface, `complete(system, prompt, schema) -> str`, implemented by `GeminiProvider`, `ClaudeProvider` (anthropic SDK, `output_config` json_schema + effort, server-side refusal `fallbacks: "default"` on Opus/Sonnet 5.x, never send `temperature`), `OpenAICompatProvider` (openai SDK with `base_url`; used for `openai`, `grok`, `custom`/OmniRoute; JSON mode downgrades schema -> object -> prompt when an endpoint rejects `response_format`), and `OllamaProvider`. All SDKs run with `max_retries=0`, because `llm.py` owns failover. `retry_after()` maps 429s (header or Gemini `retryDelay`) to a pause.
- **LLM** (`llm.py`): the primary is selected by `LLM_PROVIDER` and switched live from the UI (`set_primary`, a `providers` event). The call is hedged: after `CLOUD_TIMEOUT_S` Ollama starts in parallel and the first success wins. Only errors trip the breaker, not slowness. `llm.note` explains to the UI why the cloud is off. `SUGGESTION_SCHEMA` is closed (`additionalProperties: false`) for Claude and OpenAI strict mode. Gemini's free tier is about 20 requests/day/model, which is unusable for meetings.
- **Proactive scheduling** (`assistant.py`): debounce on participant segments, capped at `PROACTIVE_MAX_PENDING_S`. It is single-flight with one trailing re-run, so at most one proactive LLM call is in flight.
- **Knowledge paths** (`config.py`): `knowledge_base/` and `knowledge_base.json` fall back to `examples/` when absent. `KB_EXTRA_PATHS` adds files or folders. The index lives in `db_py/`.
- **State files:** running the app writes to the real `facts.json` and `sessions/`, so tests use `tmp_path`.
- UI rule from the user's global instructions: no emojis; use inline SVG icons.
