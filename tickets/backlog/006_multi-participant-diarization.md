# Ticket: Multi-participant diarization

## Meta
- ID:             006
- Typ:            feature
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/transcript.py · copilot/assistant.py

## Kontext
All remote speakers are labelled [Participant]. In calls with several people the LLM cannot tell who raised an objection, and suggestions can address the wrong person.

## Ziel
Remote speakers are separated into Participant A/B/C, which the user can rename to real names (also written to facts.json).

## Anforderungen
- Speaker embeddings per loopback chunk (e.g. a small ONNX model such as WeSpeaker/ECAPA) with online clustering.
- Labels are stable for the whole session; click to rename in the UI.
- The prompt transcript uses the names: `[Participant: Sarah] …`.
- Runs on CPU or GPU within the existing latency budget (≤ 50 ms per chunk).

## Akzeptanzkriterien
- [ ] On a 3-person test recording, ≥ 85 % of chunks get the right speaker.
- [ ] Renaming a speaker updates past transcript lines and future prompts.

## Risiken
- Short 1.5-2.5 s chunks give weak embeddings, so smoothing over neighbouring chunks may be needed.

## Offene Fragen
- Could platform captions (Teams/Meet speaker names) be used instead of audio-based diarization?

## Erste Umsetzungsidee
Compute embeddings in the STT worker after transcription. Assign by cosine similarity to running centroids, and create a new speaker below a threshold.

## Kommentare
