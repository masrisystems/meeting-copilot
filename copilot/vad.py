"""Streaming Silero VAD (ONNX, bundled with faster-whisper) and the speech chunker.

Each audio source gets its own SileroStream + Chunker running in a thread.
Chunks are cut at speech boundaries into 1.5-2.5 s pieces and put on a shared
queue for the STT worker.
"""

import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config

CONTEXT = 64  # samples of the previous frame the v6 model expects in front of each frame


def _model_path():
    import faster_whisper

    return Path(faster_whisper.__file__).parent / "assets" / "silero_vad_v6.onnx"


class SileroStream:
    def __init__(self):
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(
            str(_model_path()), providers=["CPUExecutionProvider"], sess_options=opts
        )
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)

    def __call__(self, frame):
        x = np.concatenate([self._context, frame])[None, :].astype(np.float32)
        out, self._h, self._c = self._session.run(None, {"input": x, "h": self._h, "c": self._c})
        self._context = frame[-CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])


@dataclass
class Chunk:
    source: str
    audio: np.ndarray
    duration: float
    t_end: float  # time.monotonic() when the chunk was cut


class Chunker:
    """Pure segmentation logic: feed (frame, speech_prob), get finished chunks back."""

    def __init__(self, source, frame=config.VAD_FRAME, sr=config.SAMPLE_RATE, clock=time.monotonic):
        self.source = source
        self.frame = frame
        self.sr = sr
        self.clock = clock
        fps = sr / frame
        self.min_frames = int(config.CHUNK_MIN_S * fps)
        self.max_frames = int(config.CHUNK_MAX_S * fps)
        self.silence_frames = max(1, int(config.SILENCE_END_S * fps))
        self.split_window = max(1, int(0.3 * fps))
        self.min_speech_frames = max(1, int(config.MIN_UTTERANCE_S * fps))
        self.threshold = config.VAD_THRESHOLD
        self._preroll = deque(maxlen=max(1, int(config.PRE_ROLL_S * fps)))
        self._buf = []  # list of (frame, is_speech)
        self._silence = 0

    @property
    def in_speech(self):
        return bool(self._buf)

    def feed(self, frame, prob):
        speech = prob > self.threshold
        if not self._buf:
            if speech:
                self._buf = [(f, False) for f in self._preroll] + [(frame, True)]
                self._preroll.clear()
                self._silence = 0
            else:
                self._preroll.append(frame)
            return []

        self._buf.append((frame, speech))
        self._silence = 0 if speech else self._silence + 1
        n = len(self._buf)

        # A pause ends the chunk once it is long enough; a short chunk waits for a
        # longer pause so brief hesitations don't fragment a sentence.
        if (self._silence >= self.silence_frames and n >= self.min_frames) or \
                self._silence >= 2 * self.silence_frames:
            out = self._emit(self._buf)
            self._buf = []
            self._silence = 0
            return out

        if n >= self.max_frames:
            window = self._buf[-self.split_window:]
            energies = [float(np.mean(f * f)) for f, _ in window]
            cut = n - self.split_window + int(np.argmin(energies)) + 1
            head, self._buf = self._buf[:cut], self._buf[cut:]
            if not any(s for _, s in self._buf):
                self._silence = len(self._buf)
            return self._emit(head)
        return []

    def flush(self):
        out = self._emit(self._buf) if self._buf else []
        self._buf = []
        self._silence = 0
        return out

    def _emit(self, items):
        if sum(1 for _, s in items if s) < self.min_speech_frames:
            return []
        audio = np.concatenate([f for f, _ in items])
        return [Chunk(self.source, audio, len(audio) / self.sr, self.clock())]


class VadWorker(threading.Thread):
    """Drains one audio source queue, frames it, runs VAD and emits chunks."""

    def __init__(self, source, out_queue, stop_event):
        super().__init__(daemon=True, name=f"vad-{source.label}")
        self.source = source
        self.out = out_queue
        self.stop_event = stop_event
        self.vad = SileroStream()
        self.chunker = Chunker(source.label)

    def run(self):
        import queue

        pending = np.zeros(0, dtype=np.float32)
        F = config.VAD_FRAME
        while not self.stop_event.is_set():
            try:
                block = self.source.queue.get(timeout=0.2)
            except queue.Empty:
                # WASAPI loopback delivers nothing while the output is silent;
                # feed the gap as silence so an open utterance still ends.
                if self.chunker.in_speech:
                    silent = np.zeros(F, dtype=np.float32)
                    for _ in range(int(0.2 * config.SAMPLE_RATE / F)):
                        for chunk in self.chunker.feed(silent, 0.0):
                            self.out.put(chunk)
                continue
            pending = np.concatenate([pending, block])
            while len(pending) >= F:
                frame, pending = pending[:F], pending[F:]
                for chunk in self.chunker.feed(frame, self.vad(frame)):
                    self.out.put(chunk)
        for chunk in self.chunker.flush():
            self.out.put(chunk)
