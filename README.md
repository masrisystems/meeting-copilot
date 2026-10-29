# Meeting Copilot

A local, real-time AI meeting assistant for Windows, Linux and macOS. It captures your microphone and the meeting audio itself, transcribes both locally (on your NVIDIA GPU, or on the CPU), and shows short, read-aloud-ready reply suggestions in a high-contrast window next to your video call. It works with Teams, Zoom, Meet, phone apps or anything else that plays sound.

- **[User]**: your microphone
- **[Participant]**: everything your computer plays (system audio loopback), i.e. the other people in the call

<p align="center"><img src="docs/screenshot.webp" alt="Meeting Copilot window: suggestion cards, live transcript with translation, preset buttons" width="380"></p>

Speech recognition, the knowledge base and the fallback LLM all run locally. Only the prompts sent to the cloud AI you choose (if any) leave your machine.

## Features

- **Dual-stream capture** (mic + system audio: WASAPI loopback on Windows, PulseAudio/PipeWire monitor on Linux, BlackHole on macOS), Silero VAD, 1.5-2.5 s speech chunks
- **faster-whisper**: `large-v3-turbo` on CUDA (about 360 ms per chunk on an RTX 4060), or `small` on the CPU when there is no NVIDIA GPU; German and English by default, any of Whisper's ~99 languages via `LANGUAGES` (see [Languages](#languages))
- **Proactive suggestions** for questions, objections and action items (1-2 sentences, ready to say)
- **Push-to-ask**: preset buttons, global hotkeys, or a typed question ("What should I answer?")
- **Live translation** into your language (German by default) and from it into the second one
- **Meeting records**: every meeting is saved locally as a Markdown file with summary, facts, suggestions and the full transcript
- **Always on top** with adjustable opacity, built in (Windows and Linux/X11)
- **Knowledge base (RAG)** over your own Markdown/JSON notes, plus keyword flashcards
- **Facts sheet** (budgets, names, dates) extracted automatically and kept across meetings
- **Meeting memory**: a rolling summary every 5 minutes, so the AI remembers the whole meeting and not just the last few sentences
- **Bring your own AI**: Gemini, Claude, OpenAI, Grok, or any OpenAI-compatible URL (OmniRoute, OpenRouter, LiteLLM, LM Studio ...), switchable live in the window. **Local Ollama** races in after 2.5 s and takes over when offline or out of quota
- Two roles: client/sales calls (`PROMPT=system`) or job interviews and meetings at work (`PROMPT=interview`)

## Requirements

- Windows 10/11, Linux (PulseAudio or PipeWire) or macOS 12+
- **Recommended:** an NVIDIA GPU (tested on an RTX 4060 Laptop, 8 GB). Without one, Whisper runs on the CPU (see [Platform notes](#platform-notes))
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 and all dependencies, including the CUDA libraries on Windows)
- [Ollama](https://ollama.com) with a chat model for the offline fallback, by default `granite4:7b-a1b-h`. Any other installed model works too: every model you have pulled shows up in the AI menu in the window (or set `OLLAMA_MODEL`). The window warns when Ollama isn't running or the model is missing.
  ```sh
  ollama pull granite4:7b-a1b-h
  ```
- An API key for at least one cloud AI (optional; without one everything runs on Ollama). A meeting makes hundreds of requests, so free tiers such as Gemini's (about 20 requests per day per model) are not enough.
- Chrome, Edge or Brave for the app window (otherwise the default browser opens)
- **Headphones recommended.** With speakers the mic picks up the participants. The app suppresses obvious echoes, but headphones are cleaner.

## Install

The installer sets up uv, Python and all dependencies, downloads the speech and embedding models, and adds a **Meeting Copilot** icon to your desktop and app menu (Start Menu on Windows, `~/Applications` on macOS). After that, start the copilot with a double-click. No terminal needed.

**Windows** (PowerShell):
```powershell
irm https://raw.githubusercontent.com/masrisystems/meeting-copilot/main/install.ps1 | iex
```

**Linux and macOS:**
```sh
curl -LsSf https://raw.githubusercontent.com/masrisystems/meeting-copilot/main/install.sh | sh
```

The app goes to `~/meeting-copilot` (set `MEETING_COPILOT_DIR` to change that). From a cloned repo, run `./install.sh` or `install.ps1` inside it instead. Re-run the installer to update; your `.env`, notes and sessions are kept. `--uninstall` (`-Uninstall` on Windows) removes the icons.

Started from the icon, the copilot quits when you close its window, a second double-click brings back the running one, and its output goes to `sessions/copilot.log`. Ollama is not installed for you (see [Requirements](#requirements)).

## Manual setup

```sh
git clone https://github.com/masrisystems/meeting-copilot.git
cd meeting-copilot
uv sync
cp .env.example .env                 # add an AI key, pick PROMPT, add KB_EXTRA_PATHS
```

### Choosing the AI

Set the keys you have in `.env`. `LLM_PROVIDER` picks the engine at startup, and the dropdown in the window header switches it live. Providers without a key are shown greyed out.

| `LLM_PROVIDER` | Settings | Default model |
| --- | --- | --- |
| `gemini` | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_THINKING` | `gemini-3.6-flash` |
| `claude` | `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `CLAUDE_EFFORT` | `claude-opus-5-5` at `low` effort (faster: `claude-sonnet-5-5`, `claude-haiku-5-5`) |
| `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` (`OPENAI_BASE_URL` for Azure/proxies) | `gpt-6-luna` |
| `grok` | `XAI_API_KEY`, `GROK_MODEL` | `grok-4.7` |
| `custom` | `CUSTOM_BASE_URL`, `CUSTOM_MODEL`, optional `CUSTOM_API_KEY`, `CUSTOM_LABEL` | any OpenAI-compatible endpoint |
| `ollama` | `OLLAMA_MODEL` | local only, no cloud |

Example for [OmniRoute](https://github.com/diegosouzapw/OmniRoute) or another gateway:
```ini
LLM_PROVIDER=custom
CUSTOM_LABEL=OmniRoute
CUSTOM_BASE_URL=http://localhost:20128/v1
CUSTOM_MODEL=gh/gpt-5.1-codex
```

Suggestions use structured JSON output. If an OpenAI-compatible endpoint doesn't support JSON schemas, the app falls back to plain JSON mode and then to prompt-only JSON automatically. For Claude, refusals on the current Opus/Sonnet models are retried on a fallback model server-side (`fallbacks: "default"`).

The first run downloads the Whisper model (`large-v3-turbo`, about 1.6 GB) and the German embedding model.

### Your own knowledge

The knowledge base starts empty, and the window shows a hint until you add a file. Put your notes as Markdown (`.md`) or JSON files into `knowledge_base/`: products, prices, FAQ, your CV, interview prep. `examples/` has a sample agency FAQ, a sales playbook and flashcards to copy from.

| Path | Content | Git |
| --- | --- | --- |
| `knowledge_base/` | Your `.md` / `.json` notes (searched semantically, top 3 per prompt) | ignored |
| `knowledge_base.json` | Keyword flashcards, injected when the keyword is spoken | ignored |
| `KB_EXTRA_PATHS` in `.env` | Any other files or folders, e.g. interview prep in your notes vault | — |
| `facts.json` | Facts collected during meetings | ignored |

The folder button in the window header opens `knowledge_base/`, and the sliders button opens `.env` (changes apply after a restart). The index is brought up to date when the app starts and whenever a knowledge file changes while it runs, so there is nothing to rebuild by hand.

### Platform notes

| | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Meeting audio | WASAPI loopback of the default output, built in | Monitor of the default output (`@DEFAULT_MONITOR@`) via `parec`; install `pulseaudio-utils` (Arch: `libpulse`) if it is missing | No built-in loopback. Install [BlackHole](https://github.com/ExistentialAudio/BlackHole) (2ch), create a Multi-Output Device with your speakers/headphones + BlackHole in Audio MIDI Setup, and select it as the output. `LOOPBACK_DEVICE` defaults to `BlackHole` |
| Whisper on GPU | NVIDIA, CUDA libraries installed by `uv sync` | NVIDIA, with CUDA 12 cuBLAS + cuDNN 9 installed system-wide ([faster-whisper docs](https://github.com/SYSTRAN/faster-whisper#gpu)) | not supported (CPU) |
| Global hotkeys | yes | X11 only (not Wayland) | yes, after granting the terminal Accessibility access |
| Always on top / opacity | yes | X11 only (not Wayland); opacity needs a compositing window manager | no (use a tool such as Rectangle Pro or Afloat) |
| Permissions | none | none | Microphone access for the terminal |

`WHISPER_DEVICE`, `WHISPER_MODEL` and `WHISPER_COMPUTE` default to `auto`: `large-v3-turbo` in `float16` when CUDA is usable, otherwise `small` in `int8` on the CPU. To keep up, a 2.5 s chunk must be transcribed in well under 2.5 s (about 1.2 s when both sides talk at once). Check your machine with `uv run python -m copilot.stt_bench recording.wav`. If it is too slow, set `WHISPER_MODEL=base` (faster, less accurate), fix `WHISPER_LANGUAGE` to skip language detection, and close other CPU-heavy apps. Apple Silicon and recent desktop CPUs usually manage `small`; low-power laptop CPUs may need `base`.

Example for a job interview:
```ini
PROMPT=interview
KB_EXTRA_PATHS=C:/Users/me/Notes/Interview Prep.md;C:/Users/me/Notes/CV.md
```

### Languages

Whisper transcribes about 99 languages. `LANGUAGES` (default `de,en`) lists the ones spoken in your meetings, yours first. Language detection picks only among these, which avoids misdetections, and translation goes into the first one (and from it into the second). Examples:

```ini
LANGUAGES=de,en,tr        # German speaker, meetings in German, English and Turkish
LANGUAGES=ar,en           # Arabic speaker; English segments are translated into Arabic
WHISPER_LANGUAGE=fr       # only French: skips detection, a little faster
REPLY_LANGUAGE=en         # force English suggestions (by default: German, or the interviewer's language)
```

The suggestion prompts are written for German client calls and German/English interviews; the window labels are German. For notes in other languages, switch to a multilingual embedding model, e.g. `EMBEDDING_MODEL=intfloat/multilingual-e5-large` (the index is rebuilt automatically). The `small` and `base` CPU models are less accurate for languages with little training data.

## Run

```sh
uv run python -m copilot
```

This opens a chromeless window at `http://127.0.0.1:8765`. The copilot does not listen until you click **Meeting starten**. Each meeting gets a fresh transcript and memory (the facts sheet is kept), and no audio device is opened while no meeting runs. **Mikro an/aus** mutes only your microphone; the participants are still transcribed. The window uses Chrome, Edge, Brave or Chromium (otherwise the default browser). It stays on top of the call by default: the pin button in the header (`Ctrl+Alt+O`) toggles that, and the slider next to it sets the opacity (40-100 %). Both are remembered in `settings.json`. When you stop a meeting (or quit during one), it is saved as `meetings/Meeting <date> <time>.md`: final summary, facts, every suggestion and answer, and the full transcript. The document button in the header opens the folder. Set `MEETINGS_DIR` in `.env` to collect them somewhere else, e.g. in your Obsidian vault. Options:

| Flag | Effect |
| --- | --- |
| `--start` | Start the meeting (listening) right away |
| `--fresh-facts` | Start the meeting with an empty facts sheet (overwrites `facts.json` on the first new fact) |
| `--no-browser` | Don't open the window (open the URL yourself) |
| `--no-hotkeys` | Don't register global hotkeys |
| `--exit-on-close` | Quit 10 s after the window is closed (used by the desktop icon) |
| `--log FILE` | Append output to `FILE` instead of the terminal (used by the desktop icon) |

### Buttons and hotkeys

| Keys | Action |
| --- | --- |
| `Ctrl+Alt+1` | "Was soll ich jetzt antworten?" (also a button) |
| `Ctrl+Alt+2` | "Zeig die Schwächen in ihrem Angebot bzw. Argument auf." (also a button) |
| `Ctrl+Alt+3` | "Fasse die letzten Minuten in 2 Sätzen zusammen." (also a button) |
| `Ctrl+Alt+Space` | Focus the question box to type your own question |
| `Ctrl+Alt+T` | Toggle live translation |
| `Ctrl+Alt+P` | Pause/resume proactive suggestions |
| `Ctrl+Alt+M` | Mute/unmute your microphone (the participants are still heard) |
| `Ctrl+Alt+O` | Keep the window on top, or not |

Presets (label, question, hotkey) are defined once in `PRESETS` in `copilot/config.py`.

## How it works

```
mic ──────┐                                                                   ┌─ proactive suggestions
          ├─ Silero VAD ─ 1.5–2.5 s chunks ─ faster-whisper ─ transcript ─┼─ push-to-ask answers
loopback ─┘   (per source)                      (CUDA)       (turns)      └─ translations
                                                                  │
                facts.json + meeting memory (5 min) + knowledge base (RAG) + last 15 turns
                                                                  │
              Gemini | Claude | OpenAI | Grok | custom URL ──(> 2.5 s: race)── Ollama
```

- **Chunking:** a chunk ends at a pause once it is at least 1.5 s long, and is force-cut at 2.5 s at the quietest point. Short replies ("Ja.") are still sent once the speaker stops.
- **Transcription:** the language (one of `LANGUAGES`) is detected per speaker and re-checked every few chunks. Detecting on every chunk would add about 240 ms.
- **Proactive mode** reacts only to participant speech, once the participant has finished a turn (`PROACTIVE_PAUSE`, 1.5 s without new speech; during a monologue at the latest after `PROACTIVE_MAX_WAIT`, 15 s). It shows at most 2 suggestions (`SINGLE_SUGGESTION=true`: 1, larger), stays quiet when the model reports `trigger: None`, and skips a card whose trigger repeats the one on screen within 60 s. Your own speech goes into the context but never triggers a suggestion.
- **Reading time:** a card stays on top for at least `SUGGESTION_READ_S` (10 s), and while you are speaking (plus 1.5 s), so you can read it aloud. A newer suggestion waits meanwhile ("Neuer Vorschlag wartet", click to show it now; only the newest is kept). Click a card to pin it until you click it again. Answers to your own questions always show at once.
- **Facts:** the model returns `new_facts`. They are merged into `facts.json`, with lists combined rather than overwritten.
- **Memory:** every `MEMORY_INTERVAL` seconds (default 300), the new part of the transcript is folded into a running summary covering the discussion, decisions, open points and mood. It is sent as `[MEMORY]` with every prompt, shown under "Gedächtnis" in the window, and saved as `sessions/<timestamp>.memory.md`.
- **Failover:** if the cloud AI hasn't answered within 2.5 s (`CLOUD_TIMEOUT`), Ollama starts in parallel and the first finished answer wins. If the cloud errors, the request goes to Ollama straight away. On repeated errors the cloud AI pauses for 60 s; on a rate-limit or quota error (429) it pauses for as long as the provider says. The header shows which engine answered and why the cloud is off.
- **Session log:** every transcript segment is appended to `sessions/<timestamp>.jsonl`, one file per meeting.

The prompts are in `copilot/prompts/` (`system.md` for client calls, `interview.md` for interviews).

## Tools

```sh
uv run python -m copilot.devices                   # list audio devices, live level meters
uv run python -m copilot.stt_bench recording.wav   # VAD chunks + Whisper latency on a WAV file
uv run python -m copilot.rag search "Was kostet eine Website?"
uv run python -m copilot.rag index --watch         # re-index when knowledge files change (the app does this itself)
uv run pytest                                      # unit tests (no audio/GPU needed)
```

To use a device other than the system default, set `MIC_DEVICE` / `LOOPBACK_DEVICE` in `.env` to part of its name, as shown by `copilot.devices`.

## Consent and privacy

This tool transcribes other people's speech. Recording or transcribing a conversation without the participants' consent may be illegal where you live (in Germany, for example, §201 StGB). Tell participants and get their consent. Transcripts, memory, meeting records and facts are stored locally in `sessions/`, `meetings/` (or your `MEETINGS_DIR`) and `facts.json`. With a cloud AI enabled, transcript excerpts are sent to that provider (or to your gateway).

## Roadmap

See [tickets/README.md](tickets/README.md): built-in overlay window, settings UI, streaming suggestions, echo cancellation, speaker diarization, quota control, meeting summaries and export, meeting profiles, and installers.

## License

[MIT](LICENSE) © Mohamad Masri, [Masri Systems](https://masrisystems.com)
