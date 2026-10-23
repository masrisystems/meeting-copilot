"""Dual WASAPI capture: microphone -> "User", output loopback -> "Participant".

Each source is downmixed to mono, resampled to 16 kHz float32 and pushed onto
its own queue as small numpy blocks.
"""

import queue

import numpy as np
import pyaudiowpatch as pyaudio
import soxr

from . import config

USER = "User"
PARTICIPANT = "Participant"


def _wasapi_info(p):
    return p.get_host_api_info_by_type(pyaudio.paWASAPI)


def find_mic(p, name=""):
    wasapi = _wasapi_info(p)
    if name:
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if (d["hostApi"] == wasapi["index"] and d["maxInputChannels"] > 0
                    and not d.get("isLoopbackDevice") and name.lower() in d["name"].lower()):
                return d
        raise RuntimeError(f"No WASAPI microphone matching '{name}'")
    return p.get_device_info_by_index(wasapi["defaultInputDevice"])


def find_loopback(p, name=""):
    if name:
        for d in p.get_loopback_device_info_generator():
            if name.lower() in d["name"].lower():
                return d
        raise RuntimeError(f"No loopback device matching '{name}'")
    return p.get_default_wasapi_loopback()


class Source:
    def __init__(self, p, label, device):
        self.label = label
        self.device = device
        self.queue = queue.Queue()
        self.level = 0.0  # last block RMS, read by the UI status loop
        self.channels = int(device["maxInputChannels"])
        self.rate = int(device["defaultSampleRate"])
        self._resampler = soxr.ResampleStream(self.rate, config.SAMPLE_RATE, 1, dtype="float32")
        self._stream = p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=int(self.rate * 0.02),
            stream_callback=self._callback,
            start=False,
        )

    def _callback(self, in_data, frame_count, time_info, status):
        pcm = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        if self.channels > 1:
            pcm = pcm.reshape(-1, self.channels).mean(axis=1)
        out = self._resampler.resample_chunk(pcm)
        if out.size:
            self.level = float(np.sqrt(np.mean(out * out)))
            self.queue.put(out)
        return (None, pyaudio.paContinue)

    def start(self):
        self._stream.start_stream()

    def stop(self):
        self._stream.stop_stream()
        self._stream.close()


class Capture:
    """Owns the PyAudio instance and both sources."""

    def __init__(self, mic_name=config.MIC_DEVICE, loopback_name=config.LOOPBACK_DEVICE):
        self._p = pyaudio.PyAudio()
        self.mic = Source(self._p, USER, find_mic(self._p, mic_name))
        self.loopback = Source(self._p, PARTICIPANT, find_loopback(self._p, loopback_name))
        self.sources = [self.mic, self.loopback]

    def start(self):
        for s in self.sources:
            s.start()

    def stop(self):
        for s in self.sources:
            s.stop()
        self._p.terminate()
