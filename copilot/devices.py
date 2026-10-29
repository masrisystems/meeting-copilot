"""List audio devices and show live levels for the chosen mic + loopback.

    python -m copilot.devices
"""

import subprocess
import sys
import time

from .audio import Capture

HINT = "use a name substring for MIC_DEVICE / LOOPBACK_DEVICE in .env"


def list_devices():
    if sys.platform == "win32":
        import pyaudiowpatch as pyaudio

        p = pyaudio.PyAudio()
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        print(f"WASAPI devices ({HINT}):")
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi:
                continue
            kind = "loopback" if d.get("isLoopbackDevice") else "input" if d["maxInputChannels"] else "output"
            print(f"  [{kind:8}] {d['name']}  ({int(d['defaultSampleRate'])} Hz, {d['maxInputChannels']} in)")
        p.terminate()
    elif sys.platform == "darwin":
        import sounddevice as sd

        print(f"Input devices ({HINT}; the loopback is a virtual device such as BlackHole):")
        for d in sd.query_devices():
            if d["max_input_channels"] > 0:
                print(f"  {d['name']}  ({int(d['default_samplerate'])} Hz, {d['max_input_channels']} in)")
    else:
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True).stdout
        print(f"PulseAudio/PipeWire sources ({HINT}; '.monitor' = loopback of an output):")
        for line in out.splitlines():
            name = line.split("\t")[1]
            kind = "loopback" if name.endswith(".monitor") else "input"
            print(f"  [{kind:8}] {name}")


def _bar(level):
    return "#" * min(30, int(level ** 0.5 * 90))


if __name__ == "__main__":
    list_devices()
    cap = Capture()
    print(f"\nMic: {cap.mic.device['name']}\nLoopback: {cap.loopback.device['name']}\nCtrl+C to stop.\n")
    cap.start()
    try:
        while True:
            print(f"\rUser {_bar(cap.mic.level):30} | Participant {_bar(cap.loopback.level):30}", end="")
            cap.mic.level = cap.loopback.level = 0.0
            for s in cap.sources:  # drain queues; this tool only shows levels
                while not s.queue.empty():
                    s.queue.get_nowait()
            time.sleep(0.1)
    except KeyboardInterrupt:
        cap.stop()
