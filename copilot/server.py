"""FastAPI app: serves the single-page UI and pushes events over one WebSocket."""

import asyncio
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from . import config

# Event types replayed to a newly connected (or reloaded) page.
_HISTORY_TYPES = {"segment", "retract", "translation", "suggestion", "answer"}


class Hub:
    def __init__(self):
        self.clients = set()
        self.history = deque(maxlen=300)
        self.state = {}  # latest event per sticky type (mode, facts, engine, stt)

    def publish(self, event):
        """Call on the event loop."""
        if event["type"] in _HISTORY_TYPES:
            self.history.append(event)
        elif event["type"] in ("mode", "facts", "memory", "engine", "stt"):
            self.state[event["type"]] = event
        for ws in list(self.clients):
            asyncio.ensure_future(self._send(ws, event))

    async def _send(self, ws, event):
        try:
            await ws.send_json(event)
        except Exception:
            self.clients.discard(ws)


def create_app(hub, assistant):
    app = FastAPI()

    @app.get("/")
    async def index():
        return FileResponse(config.WEB_DIR / "index.html")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"type": "presets", "presets": config.PRESETS})
        for event in [*hub.state.values(), *hub.history]:
            await ws.send_json(event)
        hub.clients.add(ws)
        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "ask":
                    asyncio.ensure_future(assistant.ask(msg.get("query", "")))
                elif msg.get("type") == "mode":
                    assistant.set_mode(proactive=msg.get("proactive"), translate=msg.get("translate"))
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    return app
