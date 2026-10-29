"""Entry point: python -m copilot [--start] [--fresh-facts] [--no-browser] [--no-hotkeys] [--exit-on-close] [--log FILE]"""

import argparse
import asyncio
import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import httpx
import uvicorn

from . import config
from .assistant import Assistant
from .audio import Capture
from .facts import Facts
from . import memory, rag
from .llm import LLM
from .memory import Memory
from .rag import KnowledgeBase
from .server import Hub, create_app
from .stt import SttWorker
from .transcript import Transcript
from .vad import VadWorker
from .window import Window

_BROWSERS = [
    Path(os.environ.get("PROGRAMFILES", ""), "Google/Chrome/Application/chrome.exe"),
    Path(os.environ.get("LOCALAPPDATA", ""), "Google/Chrome/Application/chrome.exe"),
    Path(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft/Edge/Application/msedge.exe"),
    Path(os.environ.get("PROGRAMFILES", ""), "Microsoft/Edge/Application/msedge.exe"),
    Path(os.environ.get("PROGRAMFILES", ""), "BraveSoftware/Brave-Browser/Application/brave.exe"),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
] + [Path(p) for p in map(shutil.which, ("google-chrome", "chromium", "chromium-browser",
                                          "microsoft-edge", "brave-browser")) if p]


def open_window(url):
    """Chromeless app window (kept on top by window.py); default browser as last resort."""
    w, h = config.WINDOW_SIZE
    for exe in _BROWSERS:
        if exe.is_file():
            profile = config.ROOT / ".browser-profile"  # own profile so --app opens a new window
            cmd = [str(exe), f"--app={url}", f"--window-size={w},{h}", f"--user-data-dir={profile}"]
            if sys.platform.startswith("linux"):
                cmd.append("--class=MeetingCopilot")  # matches StartupWMClass of the desktop launcher
            subprocess.Popen(cmd)
            return
    webbrowser.open(url)


def port_in_use(host, port):
    import socket

    with socket.socket() as s:
        return s.connect_ex((host, port)) == 0


def is_copilot(url):
    try:
        return "<title>Meeting Copilot</title>" in httpx.get(url, timeout=2).text
    except httpx.HTTPError:
        return False


async def exit_on_close(server, hub, grace=10):
    """Stop once the window was open and then closed (no page connected for `grace` s)."""
    idle = 0
    while True:
        await asyncio.sleep(1)
        if hub.clients:
            idle = 0
        elif hub.seen:
            idle += 1
            if idle >= grace:
                print("Window closed, shutting down.")
                server.should_exit = True
                return


async def main(args):
    url = f"http://{config.HOST}:{config.PORT}"
    if port_in_use(config.HOST, config.PORT):
        if is_copilot(url):  # launched twice: show the running instance instead
            print(f"Already running at {url}, opening its window.")
            if not args.no_browser:
                open_window(url)
            return
        raise SystemExit(f"Port {config.PORT} is in use - is the copilot already running? "
                         f"Close it or set PORT in .env.")
    loop = asyncio.get_running_loop()
    hub = Hub()
    stop = threading.Event()
    chunks = queue.Queue()

    def open_capture():
        """Called on the first meeting start, so no device is touched while the app is idle."""
        capture = Capture()
        print(f"Mic:      {capture.mic.device['name']}")
        print(f"Loopback: {capture.loopback.device['name']}")
        for s in capture.sources:
            VadWorker(s, chunks, stop).start()
        return capture

    llm = LLM()
    assistant = Assistant(Transcript(), Facts(fresh=args.fresh_facts), KnowledgeBase(), llm, hub.publish,
                          Memory(), open_capture)
    assistant.set_mode(proactive=True, translate=False)
    hub.publish(llm.describe())
    print(f"LLM:      {llm.primary.label} ({llm.primary.model}), fallback Ollama ({llm.fallback.model})")
    hub.publish({"type": "facts", "text": assistant.facts.render()})
    hub.publish({"type": "stt", "ready": False})
    hub.publish(assistant.meeting_state())
    window = Window()
    hub.publish(window.state())

    SttWorker(
        chunks,
        on_segment=lambda seg: loop.call_soon_threadsafe(assistant.on_segment, seg),
        stop_event=stop,
        on_ready=lambda: loop.call_soon_threadsafe(hub.publish, {"type": "stt", "ready": True}),
    ).start()
    if args.start:
        assistant.start_meeting()

    if not args.no_hotkeys:
        from . import hotkeys

        hotkeys.register(loop, assistant, hub.publish, window)

    asyncio.ensure_future(llm.warmup())
    asyncio.ensure_future(memory.run(assistant))
    asyncio.ensure_future(rag.watch(assistant.kb, hub.publish))

    async def ollama_status():
        while True:
            if await llm.check_ollama():
                hub.publish(llm.describe())
            await asyncio.sleep(30)

    asyncio.ensure_future(ollama_status())

    async def levels():
        while True:
            capture = assistant.capture
            hub.publish({"type": "levels", "User": round(capture.mic.level, 4) if capture else 0,
                         "Participant": round(capture.loopback.level, 4) if capture else 0,
                         "user_speaking": bool(capture and capture.mic.speaking),
                         "cloud_note": llm.note})
            if capture:
                capture.mic.level = capture.loopback.level = 0.0
            await asyncio.sleep(0.25)

    asyncio.ensure_future(levels())

    server = uvicorn.Server(uvicorn.Config(create_app(hub, assistant, window), host=config.HOST,
                                           port=config.PORT, log_level="warning"))
    if args.exit_on_close:
        asyncio.ensure_future(exit_on_close(server, hub))
    print(f"UI:       {url}")
    if not args.no_browser:
        loop.call_later(1.0, open_window, url)
    try:
        await server.serve()
    finally:
        if assistant.active:  # quit during a meeting: still save it
            await assistant.stop_meeting()
        stop.set()
        if assistant.capture:
            assistant.capture.stop()


def cli():
    p = argparse.ArgumentParser(prog="python -m copilot")
    p.add_argument("--start", action="store_true", help="start the meeting (listening) right away")
    p.add_argument("--fresh-facts", action="store_true", help="start with an empty facts sheet")
    p.add_argument("--no-browser", action="store_true", help="don't open the UI window")
    p.add_argument("--no-hotkeys", action="store_true", help="don't register global hotkeys")
    p.add_argument("--exit-on-close", action="store_true", help="quit when the UI window is closed")
    p.add_argument("--log", metavar="FILE", help="append output to FILE instead of the terminal")
    args = p.parse_args()
    if args.log:  # desktop launchers have no terminal (pythonw on Windows has no stdout at all)
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(args.log, "a", encoding="utf-8", buffering=1)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
