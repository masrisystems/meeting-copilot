# Ticket: Transcription chunk boundaries

## Meta
- ID:             004
- Typ:            feature
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/vad.py (Chunker) · copilot/stt.py · copilot/stt_bench.py

## Kontext
The 2.5 s force-cut sometimes splits a word: "fünfzehntausend" became "… von 5 Euro" + "auf 10.000 Euro" in the benchmark. Short chunks also give Whisper little context, and occasional GPU contention with Ollama causes spikes of about 1.2 s.

## Ziel
No words are lost or mangled at chunk boundaries, latency stays the same (about 360 ms median per chunk), and the transcript shown in the UI is corrected once more audio is available.

## Anforderungen
- Overlap of about 0.5 s between force-cut chunks, de-duplicated by text alignment (LocalAgreement-style).
- Re-transcribe the full utterance in the background once it ends, and replace the chunk texts in the UI (`segment_update` event).
- Custom vocabulary (`hotwords`/initial prompt) built from facts.json and knowledge-base keywords (client names, product terms).
- Optional `int8_float16` compute type and `distil-large-v3` for smaller GPUs; document the VRAM figures.
- Benchmark set: 5 recorded German and English clips with reference transcripts, and a WER report in `stt_bench`.

## Akzeptanzkriterien
- [ ] WER on the benchmark set improves by ≥ 15 % against the current chunking.
- [ ] Median per-chunk latency stays ≤ 400 ms on an RTX 4060.
- [ ] Corrected utterances visibly replace the provisional text without flicker.

## Risiken
- The re-transcription pass doubles GPU load during long monologues.

## Offene Fragen
- Should corrected text re-trigger a proactive suggestion?

## Erste Umsetzungsidee
Keep a ring buffer per source. On a force-cut, emit [t-2.5 s, t] and keep the last 0.5 s as the prefix of the next chunk. Merge with longest-common-prefix matching on words.

## Kommentare
