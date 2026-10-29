# Ticket: Linux and macOS support

## Meta
- ID:             011
- Typ:            feature
- Status:         completed
- Erstellt:       2026-10-29
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/audio.py · copilot/stt.py · copilot/hotkeys.py · pyproject.toml · .github/workflows/ci.yml

## Kontext
The copilot only ran on Windows with an NVIDIA GPU: WASAPI capture (`pyaudiowpatch`), `pywin32`, `keyboard` and the CUDA DLL setup were hard dependencies, so `uv sync` failed on Linux and macOS. That blocks most open-source users.

## Ziel
`uv sync` and `uv run python -m copilot` work on Windows, Linux and macOS, with or without an NVIDIA GPU.

## Anforderungen
- System audio capture per platform: WASAPI loopback (Windows), PulseAudio/PipeWire monitor via `parec` (Linux), a virtual device such as BlackHole (macOS).
- Windows-only packages behind `sys_platform` markers; `sounddevice` (macOS) and `pynput` (Linux/macOS) added.
- `WHISPER_DEVICE/MODEL/COMPUTE=auto`: CUDA `large-v3-turbo`/`float16`, else CPU `small`/`int8`.
- Global hotkeys via `pynput` outside Windows; failures fall back to the buttons.
- App window and `copilot.devices` work on all three platforms.
- CI runs the tests and import checks on Windows, Linux and macOS.

## Akzeptanzkriterien
- [x] `uv sync --locked` and `uv run pytest` pass on all three OSes in CI.
- [x] On Ubuntu 24.04 (PipeWire, no NVIDIA GPU) mic and loopback are transcribed end to end.
- [ ] Tested on a Mac with BlackHole.

## Risiken
- CPU transcription keeps up only on fast CPUs; low-power laptops need `WHISPER_MODEL=base`.
- macOS needs a manual BlackHole setup; Linux hotkeys don't work on Wayland.

## Offene Fragen
- Should macOS capture system audio natively via ScreenCaptureKit (macOS 13+) instead of BlackHole?

## Erste Umsetzungsidee
One `_Source` interface in `audio.py` with a backend per platform; config defaults resolve to the hardware at startup.

## Kommentare
- 2026-10-29: On an i5-1335U, `small` needs about 2.3-2.9 s per 2.5 s chunk (not real-time with other load), `base` about 0.75 s.
