"""Run a recording through the real VAD chunker + Whisper and print per-chunk latency.

    python -m copilot.stt_bench path/to/audio.wav [Participant|User]
"""

import sys
import time

import numpy as np

from . import config
from .stt import Transcriber
from .vad import Chunker, SileroStream


def read_wav(path):
    import wave

    import soxr

    with wave.open(path, "rb") as w:
        rate, channels = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return soxr.resample(pcm, rate, config.SAMPLE_RATE).astype(np.float32)


def main(path, source="Participant"):
    audio = read_wav(path)
    vad, chunker = SileroStream(), Chunker(source)
    F = config.VAD_FRAME
    chunks = []
    for i in range(0, len(audio) - F + 1, F):
        frame = audio[i:i + F]
        chunks += chunker.feed(frame, vad(frame))
    chunks += chunker.flush()
    print(f"{len(chunks)} chunks, durations {[round(c.duration, 2) for c in chunks]}")

    t = time.perf_counter()
    stt = Transcriber()
    print(f"model load {time.perf_counter() - t:.1f}s")
    stt.transcribe(chunks[0])  # warm-up (CUDA kernels)

    lat = []
    for c in chunks:
        t = time.perf_counter()
        seg = stt.transcribe(c)
        lat.append(time.perf_counter() - t)
        print(f"{c.duration:4.2f}s audio  {lat[-1] * 1000:5.0f} ms  [{seg.lang if seg else '--'}] {seg.text if seg else ''}")
    print(f"\nmedian {np.median(lat) * 1000:.0f} ms, p95 {np.percentile(lat, 95) * 1000:.0f} ms")


if __name__ == "__main__":
    main(*sys.argv[1:])
