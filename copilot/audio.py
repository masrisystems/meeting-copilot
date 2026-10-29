"""Dual capture: microphone -> "User", system output -> "Participant".

- Windows: WASAPI mic + WASAPI loopback of the default output (pyaudiowpatch).
- Linux: PulseAudio/PipeWire via `parec`; the default sink's `.monitor` source is the loopback.
- macOS: CoreAudio via sounddevice; the loopback is a virtual device such as BlackHole.

Each source is downmixed to mono, resampled to 16 kHz float32 and pushed onto
its own queue as small numpy blocks.
"""

import queue
import shutil
import subprocess
import sys
import threading

import numpy as np
import soxr

from . import config

if sys.platform == "win32":
    import pyaudiowpatch as pyaudio
elif sys.platform == "darwin":
    import sounddevice as sd

USER = "User"
PARTICIPANT = "Participant"
MAC_LOOPBACK_DEFAULT = "BlackHole"


class _Source:
    """Shared by all backends: `device["name"]`, a queue of 16 kHz mono blocks, the last RMS.
    start()/stop() open and close the device and may be called repeatedly (mute, stop meeting)."""

    def __init__(self, label, device):
        self.label = label
        self.device = device
        self.queue = queue.Queue()
        self.level = 0.0  # last block RMS, read by the UI status loop
        self.speaking = False  # VAD is inside an utterance, set by VadWorker

    def _push(self, out):
        if out.size:
            self.level = float(np.sqrt(np.mean(out * out)))
            self.queue.put(out)


# --- Windows (WASAPI) -----------------------------------------------------------

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


class Source(_Source):
    def __init__(self, p, label, device):
        super().__init__(label, device)
        self._p = p
        self.channels = int(device["maxInputChannels"])
        self.rate = int(device["defaultSampleRate"])
        self._stream = None

    def _callback(self, in_data, frame_count, time_info, status):
        pcm = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        if self.channels > 1:
            pcm = pcm.reshape(-1, self.channels).mean(axis=1)
        self._push(self._resampler.resample_chunk(pcm))
        return (None, pyaudio.paContinue)

    def start(self):
        if self._stream:
            return
        self._resampler = soxr.ResampleStream(self.rate, config.SAMPLE_RATE, 1, dtype="float32")
        self._stream = self._p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=self.device["index"],
            frames_per_buffer=int(self.rate * 0.02),
            stream_callback=self._callback,
        )

    def stop(self):
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None


# --- Linux (PulseAudio / PipeWire) ------------------------------------------------

def find_pulse_source(name, default):
    """Full name of the source whose name contains `name`; empty = `default`."""
    if not name:
        return default
    out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        source = line.split("\t")[1]
        if name.lower() in source.lower():
            return source
    raise RuntimeError(f"No PulseAudio/PipeWire source matching '{name}'")


class ParecSource(_Source):
    """`parec` delivers 16 kHz mono float32 directly; PulseAudio does the resampling."""

    BLOCK = int(config.SAMPLE_RATE * 0.02)

    def __init__(self, label, name):
        super().__init__(label, {"name": name})
        self._proc = None

    def _read(self, proc):
        while data := proc.stdout.read(self.BLOCK * 4):
            self._push(np.frombuffer(data, dtype=np.float32))

    def start(self):
        if self._proc:
            return
        self._proc = subprocess.Popen(
            ["parec", f"--device={self.device['name']}", "--format=float32le",
             f"--rate={config.SAMPLE_RATE}", "--channels=1", "--raw", "--latency-msec=20"],
            stdout=subprocess.PIPE)
        threading.Thread(target=self._read, args=(self._proc,), daemon=True,
                         name=f"parec-{self.label}").start()

    def stop(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None


# --- macOS (CoreAudio) --------------------------------------------------------------

def find_sd_input(name):
    """Input device whose name contains `name`; empty = the system default input."""
    if not name:
        return sd.query_devices(kind="input")
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            return d
    raise RuntimeError(f"No input device matching '{name}'")


class SoundDeviceSource(_Source):
    def __init__(self, label, device):
        super().__init__(label, device)
        self.channels = int(device["max_input_channels"])
        self.rate = int(device["default_samplerate"])
        self._stream = None

    def _callback(self, indata, frames, time_info, status):
        pcm = indata.mean(axis=1) if self.channels > 1 else indata[:, 0]
        self._push(self._resampler.resample_chunk(np.ascontiguousarray(pcm)))

    def start(self):
        if self._stream:
            return
        self._resampler = soxr.ResampleStream(self.rate, config.SAMPLE_RATE, 1, dtype="float32")
        self._stream = sd.InputStream(
            device=self.device["index"],
            channels=self.channels,
            samplerate=self.rate,
            dtype="float32",
            blocksize=int(self.rate * 0.02),
            callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Capture:
    """Owns the platform audio backend and both sources."""

    def __init__(self, mic_name=config.MIC_DEVICE, loopback_name=config.LOOPBACK_DEVICE):
        self._p = None
        if sys.platform == "win32":
            self._p = pyaudio.PyAudio()
            self.mic = Source(self._p, USER, find_mic(self._p, mic_name))
            self.loopback = Source(self._p, PARTICIPANT, find_loopback(self._p, loopback_name))
        elif sys.platform == "darwin":
            try:
                loopback = find_sd_input(loopback_name or MAC_LOOPBACK_DEFAULT)
            except RuntimeError as e:
                raise RuntimeError(f"{e}. macOS has no built-in loopback: install BlackHole and route "
                                   f"the meeting audio to it (see README), or set LOOPBACK_DEVICE.") from None
            self.mic = SoundDeviceSource(USER, find_sd_input(mic_name))
            self.loopback = SoundDeviceSource(PARTICIPANT, loopback)
        else:
            if not shutil.which("parec"):
                raise RuntimeError("'parec' not found. Install it (Debian/Ubuntu/Fedora: pulseaudio-utils, "
                                   "Arch: libpulse).")
            self.mic = ParecSource(USER, find_pulse_source(mic_name, "@DEFAULT_SOURCE@"))
            self.loopback = ParecSource(PARTICIPANT, find_pulse_source(loopback_name, "@DEFAULT_MONITOR@"))
        self.sources = [self.mic, self.loopback]

    def start(self):
        for s in self.sources:
            s.start()

    def stop(self):
        for s in self.sources:
            s.stop()
        if self._p:
            self._p.terminate()
