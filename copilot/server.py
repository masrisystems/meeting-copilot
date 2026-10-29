"""FastAPI app: serves the single-page UI and pushes events over one WebSocket."""

import asyncio
import os
import shutil
import subprocess
import sys
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from . import config

# Event types replayed to a newly connected (or reloaded) page.
_HISTORY_TYPES = {"segment", "retract", "translation", "suggestion", "answer"}


def reveal(path, text=False):
    """Open a folder in the file manager, or a file in the text editor."""
    if sys.platform == "win32":
        subprocess.Popen(["notepad.exe", str(path)]) if text else os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-t", str(path)] if text else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def settings_file():
    env = config.ROOT / ".env"
    if not env.exists():
        shutil.copy(config.ROOT / ".env.example", env)
    return env


class Hub:
    def __init__(self):
        self.clients = set()
        self.seen = False  # a page has connected at least once (for --exit-on-close)
        self.history = deque(maxlen=300)
        self.state = {}  # latest event per sticky type (mode, facts, engine, stt)

    def publish(self, event):
        """Call on the event loop."""
        if event["type"] == "reset":  # new meeting: a reloaded page starts empty too
            self.history.clear()
        elif event["type"] in _HISTORY_TYPES:
            self.history.append(event)
        elif event["type"] in ("mode", "facts", "memory", "engine", "stt", "providers", "meeting", "kb", "window"):
            self.state[event["type"]] = event
        for ws in list(self.clients):
            asyncio.ensure_future(self._send(ws, event))

    async def _send(self, ws, event):
        try:
            await ws.send_json(event)
        except Exception:
            self.clients.discard(ws)


async def _apply_window(window):
    """The page connects as soon as the window exists; its title may still be settling, so retry briefly."""
    for _ in range(3):
        if await asyncio.to_thread(window.apply):
            return
        await asyncio.sleep(1)


def create_app(hub, assistant, window):
    app = FastAPI()

    @app.get("/")
    async def index():
        return FileResponse(config.WEB_DIR / "index.html")

    @app.get("/icon.svg")
    async def icon():
        return FileResponse(config.WEB_DIR / "icon.svg")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"type": "presets", "presets": config.PRESETS})
        await ws.send_json({"type": "ui", "read_s": config.SUGGESTION_READ_S, "single": config.SINGLE_SUGGESTION})
        for event in hub.state.values():
            await ws.send_json(event)
        for event in hub.history:  # replayed cards are shown at once, not queued for reading time
            await ws.send_json({**event, "replay": True})
        hub.clients.add(ws)
        hub.seen = True
        asyncio.ensure_future(_apply_window(window))
        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "ask":
                    asyncio.ensure_future(assistant.ask(msg.get("query", "")))
                elif msg.get("type") == "provider":
                    assistant.set_provider(msg.get("id", ""))
                elif msg.get("type") == "mode":
                    assistant.set_mode(proactive=msg.get("proactive"), translate=msg.get("translate"))
                elif msg.get("type") == "meeting":
                    assistant.start_meeting() if msg.get("active") else assistant.stop_meeting()
                elif msg.get("type") == "mic":
                    assistant.set_mic(bool(msg.get("on")))
                elif msg.get("type") == "window":
                    window.set(on_top=msg.get("on_top"), opacity=msg.get("opacity"))
                    hub.publish(window.state())
                elif msg.get("type") == "open":
                    if msg.get("what") in ("notes", "meetings"):
                        folder = config.KB_DIR if msg["what"] == "notes" else config.MEETINGS_DIR
                        folder.mkdir(parents=True, exist_ok=True)
                        reveal(folder)
                    elif msg.get("what") == "settings":
                        reveal(settings_file(), text=True)
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    return app
