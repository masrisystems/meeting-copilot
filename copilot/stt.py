"""faster-whisper worker: one GPU model, chunks from both sources, labelled segments out."""

import queue
import threading
import time
from dataclasses import dataclass, field

from . import cuda  # noqa: F401  (registers NVIDIA DLL dirs before ctranslate2 loads)
from . import config

LANGS = ("de", "en")

# Classic Whisper hallucinations on silence/noise, dropped when the model is unsure.
FILLER = {
    "untertitel im auftrag des zdf",
    "untertitel im auftrag des zdf, 2017",
    "untertitel der amara.org-community",
    "vielen dank.",
    "vielen dank fürs zuschauen.",
    "thank you.",
    "thanks for watching!",
    "bis zum nächsten mal.",
    "...",
}


@dataclass
class Segment:
    source: str
    text: str
    lang: str
    duration: float
    t_end: float
    latency: float = 0.0
    ts: float = field(default_factory=time.time)
    id: int = 0


def is_hallucination(text, no_speech_prob, avg_logprob):
    norm = text.strip().lower()
    if not norm or all(not c.isalnum() for c in norm):
        return True
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return True
    return norm in FILLER and no_speech_prob > 0.2


class Transcriber:
    def __init__(self):
        from faster_whisper import WhisperModel

        args = (config.WHISPER_MODEL,)
        kwargs = {"device": config.WHISPER_DEVICE, "compute_type": config.WHISPER_COMPUTE}
        try:
            # The Hugging Face freshness check can take minutes on a slow link; the
            # cached model loads in ~4 s.
            self.model = WhisperModel(*args, local_files_only=True, **kwargs)
        except Exception:
            self.model = WhisperModel(*args, **kwargs)  # first run: download
        self._last_text = {}
        self._lang = {}  # sticky language per source
        self._count = {}

    def _language(self, chunk):
        """Language detection costs a full extra encoder pass (~240 ms on a 4060), so it
        runs only on every Nth chunk that is long enough to detect reliably."""
        if config.WHISPER_LANGUAGE != "auto":
            return config.WHISPER_LANGUAGE
        n = self._count[chunk.source] = self._count.get(chunk.source, 0) + 1
        lang = self._lang.get(chunk.source)
        if lang is None or (n % config.LANG_DETECT_EVERY == 0 and chunk.duration >= config.CHUNK_MIN_S):
            _, _, probs = self.model.detect_language(chunk.audio)
            probs = dict(probs)
            # The meetings are German/English only; pick the likelier of the two.
            lang = max(LANGS, key=lambda l: probs.get(l, 0.0))
            self._lang[chunk.source] = lang
        return lang

    def transcribe(self, chunk):
        prompt = self._last_text.get(chunk.source, "")[-config.PROMPT_CONTEXT_CHARS:]
        lang = self._language(chunk)
        segs, _ = self.model.transcribe(
            chunk.audio,
            language=lang,
            beam_size=1,
            temperature=0.0,  # no fallback re-decodes; they double latency on short chunks
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=prompt or None,
        )

        text = " ".join(
            s.text.strip() for s in segs if not is_hallucination(s.text, s.no_speech_prob, s.avg_logprob)
        ).strip()
        if not text:
            return None
        self._last_text[chunk.source] = (self._last_text.get(chunk.source, "") + " " + text)[-1000:]
        return Segment(chunk.source, text, lang, chunk.duration, chunk.t_end)


class SttWorker(threading.Thread):
    def __init__(self, in_queue, on_segment, stop_event, on_ready=None):
        super().__init__(daemon=True, name="stt")
        self.in_queue = in_queue
        self.on_segment = on_segment
        self.stop_event = stop_event
        self.on_ready = on_ready

    def run(self):
        transcriber = Transcriber()
        # First CUDA call compiles kernels (~5 s); do it before the meeting audio arrives.
        import numpy as np

        list(transcriber.model.transcribe(np.zeros(config.SAMPLE_RATE, dtype=np.float32), language="de")[0])
        if self.on_ready:
            self.on_ready()
        while not self.stop_event.is_set():
            try:
                chunk = self.in_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            seg = transcriber.transcribe(chunk)
            if seg:
                seg.latency = time.monotonic() - chunk.t_end
                self.on_segment(seg)
