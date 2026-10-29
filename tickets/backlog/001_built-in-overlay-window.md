# Ticket: Built-in overlay window

## Meta
- ID:             001
- Typ:            feature
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/__main__.py (open_window) · copilot/hotkeys.py (focus_window) · copilot/web/index.html

## Kontext
The UI currently opens as a Chrome/Edge `--app` window. Keeping it on top and see-through requires the third-party tool WindowTop. That is an extra install, it's configured per window, and its settings are lost when the window is recreated.

## Ziel
The copilot opens its own native window that can stay on top, has adjustable opacity and remembers its position. It needs no external tool and no installed browser.

## Anforderungen
- Native window hosting the existing web UI (e.g. `pywebview` with the Edge WebView2 runtime that ships with Windows 11).
- Always-on-top toggle in the header and via hotkey (`Ctrl+Alt+O`).
- Opacity slider (40-100 %), persisted.
- Optional click-through mode (`WS_EX_TRANSPARENT | WS_EX_LAYERED`), toggled by hotkey, so the window never steals clicks from the call.
- Window size, position and monitor are restored on the next start.
- `--browser` flag keeps the current browser-window behaviour as a fallback.
- No capture-exclusion or anti-screen-share code (out of scope by design).

## Akzeptanzkriterien
- [ ] `uv run python -m copilot` opens a native window without Chrome/Edge installed.
- [ ] Always-on-top, opacity and click-through work and survive a restart.
- [ ] `Ctrl+Alt+Space` still focuses the question box in the native window.
- [ ] WindowTop is no longer mentioned as a requirement in the README.

## Risiken
- WebView2 runtime missing on older Windows 10 machines, so a fallback to browser mode is needed.
- In click-through mode the user cannot click the buttons; it needs a clear visual indicator and a hotkey to leave it.

## Offene Fragen
- `pywebview` (small, Python-only) or a Tauri shell (smaller footprint, but adds a Rust toolchain)?

## Erste Umsetzungsidee
Start uvicorn in a background thread and open `webview.create_window(..., on_top=True)` on the main thread. Use `win32gui.SetLayeredWindowAttributes` for opacity and extended styles for click-through. Store the window state in `settings.json`.

## Kommentare
- 2026-10-29: Always-on-top (`Ctrl+Alt+O`, pin button) and opacity (40-100 %, slider) for the existing app window, persisted in `settings.json` (`copilot/window.py`): Win32 API on Windows, EWMH hints on Linux/X11. WindowTop is no longer needed there. Open: a native window without a browser (pywebview), click-through, restoring the position, Wayland and macOS.
