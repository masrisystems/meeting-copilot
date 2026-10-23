# Ticket: UI polish and settings

## Meta
- ID:             002
- Typ:            feature
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/web/index.html · copilot/config.py · .env.example

## Kontext
All tuning (devices, models, presets, timeouts) currently happens in `.env` and `copilot/config.py`, which needs a restart. The UI is functional but minimal: no font scaling, no way to copy or pin a suggestion, and the header wraps at narrow widths.

## Ziel
A user can configure and read the copilot entirely from its window. Suggestions are easier to read at a glance and to reuse.

## Anforderungen
- Settings drawer: microphone and loopback device picker (with live level preview), Gemini model, thinking level, Ollama model, language mode, proactive debounce.
- Preset editor: add, edit and reorder preset questions; hotkeys are re-bound live.
- Settings persisted to `settings.json` (secrets stay in `.env`).
- Font-size control (S/M/L/XL) and a compact mode that shows only the newest suggestion card.
- Copy-to-clipboard and "pin" on every suggestion card; pinned cards stay at the top.
- Single-line header at 380 px width; status icons get tooltips.
- Searchable session history (past suggestions and answers) in a side panel.
- Inline SVG icons only, no emoji.

## Akzeptanzkriterien
- [ ] Changing the microphone or the Gemini model in the UI takes effect without restarting the app.
- [ ] Preset edits appear as buttons and hotkeys immediately and persist.
- [ ] The UI is readable at 380 px width with no horizontal scroll.
- [ ] Copy and pin work with mouse and keyboard.

## Risiken
- Live device switching while streams are open: Capture needs a clean restart path.
- Re-registering global hotkeys at runtime can leave stale hooks.

## Offene Fragen
- Should settings also be editable from a CLI (`python -m copilot.settings`)?

## Erste Umsetzungsidee
Add a `settings.py` (dataclass plus JSON persistence) that overrides config values. Add a WebSocket `settings` message type, and add `Capture.restart()` and `hotkeys.reload()`.

## Kommentare
