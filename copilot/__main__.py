"""Entry point: python -m copilot [--fresh-facts] [--no-browser] [--no-hotkeys]"""

import argparse
import asyncio
import os
import queue
import subprocess
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import config
from .assistant import Assistant
from .audio import Capture
from .facts import Facts
from . import memory
from .llm import LLM
from .memory import Memory
from .rag import KnowledgeBase
from .server import Hub, create_app
from .stt import SttWorker
from .transcript import Transcript, new_session_path
from .vad import VadWorker

_BROWSERS = [
    Path(os.environ.get("PROGRAMFILES", ""), "Google/Chrome/Application/chrome.exe"),
    Path(os.environ.get("LOCALAPPDATA", ""), "Google/Chrome/Application/chrome.exe"),
    Path(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft/Edge/Application/msedge.exe"),
    Path(os.environ.get("PROGRAMFILES", ""), "Microsoft/Edge/Application/msedge.exe"),
    Path(os.environ.get("PROGRAMFILES", ""), "BraveSoftware/Brave-Browser/Application/brave.exe"),
]


def open_window(url):
    """Chromeless app window (pin it with WindowTop); default browser as last resort."""
    w, h = config.WINDOW_SIZE
    for exe in _BROWSERS:
        if exe.is_file():
            profile = config.ROOT / ".browser-profile"  # own profile so --app opens a new window
            subprocess.Popen([str(exe), f"--app={url}", f"--window-size={w},{h}",
                              f"--user-data-dir={profile}"])
            return
    webbrowser.open(url)


def port_in_use(host, port):
    import socket

    with socket.socket() as s:
        return s.connect_ex((host, port)) == 0


async def main(args):
    if port_in_use(config.HOST, config.PORT):
        raise SystemExit(f"Port {config.PORT} is in use - is the copilot already running? "
                         f"Close it or set PORT in .env.")
    loop = asyncio.get_running_loop()
    hub = Hub()
    session = new_session_path()
    llm = LLM()
    assistant = Assistant(Transcript(session), Facts(fresh=args.fresh_facts), KnowledgeBase(), llm, hub.publish,
                          Memory(session.with_suffix(".memory.md")))
    assistant.set_mode(proactive=True, translate=False)
    hub.publish({"type": "facts", "text": assistant.facts.render()})
    hub.publish({"type": "stt", "ready": False})

    stop = threading.Event()
    capture = Capture()
    print(f"Mic:      {capture.mic.device['name']}")
    print(f"Loopback: {capture.loopback.device['name']}")
    print(f"Session:  {session}")

    chunks = queue.Queue()
    workers = [VadWorker(s, chunks, stop) for s in capture.sources]
    workers.append(SttWorker(
        chunks,
        on_segment=lambda seg: loop.call_soon_threadsafe(assistant.on_segment, seg),
        stop_event=stop,
        on_ready=lambda: loop.call_soon_threadsafe(hub.publish, {"type": "stt", "ready": True}),
    ))
    for w in workers:
        w.start()
    capture.start()

    if not args.no_hotkeys:
        from . import hotkeys

        hotkeys.register(loop, assistant, hub.publish)

    asyncio.ensure_future(llm.warmup())
    asyncio.ensure_future(memory.run(assistant))
    asyncio.ensure_future(asyncio.to_thread(assistant.kb.context, "warmup"))  # loads the embedder

    async def levels():
        while True:
            hub.publish({"type": "levels", "User": round(capture.mic.level, 4),
                         "Participant": round(capture.loopback.level, 4),
                         "gemini": llm.gemini_available,
                         "gemini_note": "" if llm.gemini_available else llm.gemini_note})
            capture.mic.level = capture.loopback.level = 0.0
            await asyncio.sleep(0.25)

    asyncio.ensure_future(levels())

    server = uvicorn.Server(uvicorn.Config(create_app(hub, assistant), host=config.HOST,
                                           port=config.PORT, log_level="warning"))
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"UI:       {url}")
    if not args.no_browser:
        loop.call_later(1.0, open_window, url)
    try:
        await server.serve()
    finally:
        stop.set()
        capture.stop()


def cli():
    p = argparse.ArgumentParser(prog="python -m copilot")
    p.add_argument("--fresh-facts", action="store_true", help="start with an empty facts sheet")
    p.add_argument("--no-browser", action="store_true", help="don't open the UI window")
    p.add_argument("--no-hotkeys", action="store_true", help="don't register global hotkeys")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
