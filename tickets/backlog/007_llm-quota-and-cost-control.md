# Ticket: LLM quota and cost control

## Meta
- ID:             007
- Typ:            feature
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/llm.py · copilot/assistant.py

## Kontext
The Gemini free tier allows only about 20 requests per day per model, and one meeting uses hundreds of calls (proactive mode every few seconds plus one call per translated segment). On 2026-10-23 the quota ran out within minutes. On the paid tier, cost is currently invisible.

## Ziel
The copilot uses as few LLM calls as needed, shows usage and estimated cost live, and degrades gracefully when limits are reached.

## Anforderungen
- Per-session counters: requests, input/output tokens per engine (from `usage_metadata`), and estimated EUR cost; shown in the header tooltip and the session summary.
- Configurable budgets: max requests per minute and per session; when exceeded, proactive mode slows down or switches to Ollama.
- Batch translation: translate per finished turn or every N seconds instead of per segment.
- Skip proactive calls when the new participant text is under N words or only filler ("ja", "okay", "genau").
- A clear UI banner explaining free-tier exhaustion, with a link to enable billing.

## Akzeptanzkriterien
- [ ] A 30-minute test call makes ≤ 50 % of the calls the current build makes, with comparable suggestions.
- [ ] Token and cost figures match the Google AI Studio usage page within 5 %.
- [ ] Reaching a budget never blocks the UI; the engine badge shows the fallback.

## Risiken
- Being too aggressive about skipping calls may miss short but important objections ("Zu teuer.").

## Offene Fragen
- Should translation always use the local model to save quota?

## Erste Umsetzungsidee
Add a `Usage` class in llm.py that records each response's usage. Add a token-bucket limiter in front of `generate()`. Add a filler-word filter in `Assistant._schedule`.

## Kommentare
