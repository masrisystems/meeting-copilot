# Ticket: Lower suggestion latency

## Meta
- ID:             003
- Typ:            feature
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/llm.py · copilot/assistant.py · copilot/prompts/system.md

## Kontext
Measured on 2026-10-23: a Gemini 3.6 Flash suggestion takes 2.1-2.8 s with the full prompt in JSON mode, and the knowledge-base search adds about 180 ms on top. In JSON mode nothing can be shown until the whole object has arrived. The Ollama fallback (granite4) takes 3-4 s.

## Ziel
The first readable suggestion text appears within about 1.2 s of the participant finishing a sentence.

## Anforderungen
- Stream the suggestion text and render it word by word. Options: a plain-text format with delimiters instead of JSON, or incremental partial-JSON parsing.
- Start knowledge-base retrieval as soon as a segment arrives (speculatively), not after the debounce.
- Use Gemini context caching for the static system prompt and knowledge snippets.
- Move fact extraction out of the suggestion call into a separate background call, so the hot path produces a shorter output.
- Benchmark script `copilot/llm_bench.py` reporting p50/p95 time to first visible word per engine/model.
- Evaluate `gemini-3.8-flash` (thinking `low`) and Flash-Lite against 3.6 Flash for latency and quality.

## Akzeptanzkriterien
- [ ] p50 time to first visible word ≤ 1.2 s on Gemini (measured with `llm_bench.py`).
- [ ] Suggestion quality is unchanged in a side-by-side review of 20 recorded triggers.
- [ ] The Ollama path still works offline.

## Risiken
- Plain-text streaming loses schema validation, so robust parsing is needed.
- Context caching has a minimum token size and costs; check whether it pays off at this prompt size.

## Offene Fragen
- Is a two-stage approach (Flash-Lite for the instant line, Flash for a refined line) worth the extra calls and quota?

## Erste Umsetzungsidee
Output format `TRIGGER: …` followed by numbered suggestion lines, streamed via `generate_content_stream`, with the UI updating the card in place. Facts are extracted by a debounced background job every N turns.

## Kommentare
