# Ticket: Echo cancellation for speakers

## Meta
- ID:             005
- Typ:            feature
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/transcript.py (_is_echo) · copilot/audio.py

## Kontext
Without headphones the microphone records the participants from the speakers. Today this is handled after transcription by fuzzy text matching, which wastes GPU time and misses partial echoes.

## Ziel
Participant audio is removed from the microphone signal before VAD, so [User] contains only the user's own voice, even on laptop speakers.

## Anforderungen
- Acoustic echo cancellation using the loopback stream as the reference signal (e.g. WebRTC AEC3 via `webrtc-audio-processing` or `speexdsp` bindings).
- Delay estimation between loopback and mic (typically 20-200 ms).
- Toggle in settings; auto-enabled when the output device is not a headset.
- Keep the text-based echo filter as a second line of defence.

## Akzeptanzkriterien
- [ ] With laptop speakers, ≥ 90 % of participant speech no longer appears as [User] segments on a recorded test call.
- [ ] Added latency ≤ 30 ms per frame; CPU ≤ 5 % on the reference laptop.

## Risiken
- Python bindings for AEC3 on Windows may need building from source.

## Offene Fragen
- Is the Windows built-in communications AEC (via the "Communications" endpoint) good enough to use instead?

## Erste Umsetzungsidee
Run AEC in the mic Source callback with the most recent loopback frames from a shared ring buffer, before the soxr resampling.

## Kommentare
