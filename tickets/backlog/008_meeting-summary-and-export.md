# Ticket: Meeting summary and export

## Meta
- ID:             008
- Typ:            feature
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/memory.py · copilot/transcript.py (sessions/*.jsonl) · copilot/facts.py

## Kontext
Each session is logged as JSONL, and a rolling meeting memory (summary every 5 min, `copilot/memory.py`) keeps the LLM aware of earlier parts of the call. There is still no end-of-meeting summary, no list of action items and no export.

## Ziel
At the end of a meeting, one click produces a Markdown summary with decisions, action items, open questions and facts, ready for Obsidian or email.

## Anforderungen
- An "End meeting" button and hotkey that close the session and generate the summary from the rolling memory plus the remaining transcript (Gemini, with Ollama fallback).
- Markdown output with frontmatter (date, participants, duration, tags), saved to `sessions/<timestamp>.md`. An optional export folder (e.g. an Obsidian vault path) is set in settings.
- Action items as Markdown tasks with owner and due date where mentioned.
- A session browser in the UI for reopening past transcripts, memories and summaries.

## Akzeptanzkriterien
- [ ] A 45-minute test session produces a summary in < 30 s that covers all decisions in a manual review.
- [ ] The export folder setting writes valid Obsidian Markdown.

## Risiken
- The memory summaries can lose details, so the final summary should re-read the session JSONL for decisions and numbers.

## Offene Fragen
- Should the summary also be sent somewhere automatically (email, Kanban)? Only with explicit user action.

## Erste Umsetzungsidee
Add an `Assistant.end_meeting()` that combines the memory with the transcript tail, asks for structured JSON (decisions, action_items, open_questions), and renders it to Markdown.

## Kommentare
- 2026-10-29: Each meeting (Meeting starten/beenden) is saved as `MEETINGS_DIR/Meeting <date> <time>.md` with the final summary, facts, suggestions/answers and the transcript (`copilot/export.py`). Open: action items, sharing/export formats, a meeting list in the UI.
