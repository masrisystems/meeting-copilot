"""Gemini Flash primary, local Ollama fallback.

If Gemini hasn't answered within GEMINI_TIMEOUT_S, Ollama is started in parallel and
the first finished answer wins. After repeated Gemini errors a breaker skips it for a
cooldown so offline mode costs no extra latency.
"""

import asyncio
import json
import re
import time

import httpx

from . import config

SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "trigger": {"type": "string"},
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "text": {"type": "string"},
                    "tone": {"type": "string"},
                },
                "required": ["intent", "text", "tone"],
            },
        },
        "new_facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
            },
        },
    },
    "required": ["trigger", "suggestions", "new_facts"],
}


def parse_json(text):
    """Strict parse first, then the outermost {...} block (models sometimes wrap JSON)."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


class LLM:
    def __init__(self):
        self._gemini = None
        self._thinking = config.GEMINI_THINKING
        self._fails = 0
        self._skip_until = 0.0
        self.gemini_note = ""  # why Gemini is currently skipped, shown in the UI
        self._http = httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT_S)
        if config.GEMINI_API_KEY:
            from google import genai

            self._gemini = genai.Client(api_key=config.GEMINI_API_KEY)
        else:
            self.gemini_note = "Kein GEMINI_API_KEY"
            print("GEMINI_API_KEY not set - using Ollama only.")

    @property
    def gemini_available(self):
        return self._gemini is not None and time.monotonic() >= self._skip_until

    async def generate(self, system, prompt, schema=None):
        """Returns (text, engine, seconds). schema set -> JSON mode.

        Gemini gets GEMINI_TIMEOUT_S on its own. If it hasn't answered by then, Ollama
        starts in parallel and whichever finishes first wins, so a slightly slow Gemini
        answer is not thrown away. Only Gemini errors (not slowness) trip the breaker.
        """
        start = time.monotonic()
        if not self.gemini_available:
            return await self._ollama_call(system, prompt, schema), "ollama", time.monotonic() - start

        tasks = {asyncio.ensure_future(self._gemini_call(system, prompt, schema)): "gemini"}
        done, _ = await asyncio.wait(tasks, timeout=config.GEMINI_TIMEOUT_S)
        if not done:
            tasks[asyncio.ensure_future(self._ollama_call(system, prompt, schema))] = "ollama"

        error = None
        while tasks:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                engine = tasks.pop(task)
                if task.exception() is None:
                    for other in tasks:
                        other.cancel()
                    if engine == "gemini":
                        self._fails = 0
                        self.gemini_note = ""
                    return task.result(), engine, time.monotonic() - start
                error = task.exception()
                if engine == "gemini":
                    self._gemini_failed(error)
                    if not tasks:  # failed before the hedge started
                        tasks[asyncio.ensure_future(self._ollama_call(system, prompt, schema))] = "ollama"
        raise error

    def _gemini_failed(self, error):
        self._fails += 1
        if getattr(error, "code", None) == 429:
            # Quota exhausted: Google says how long to wait (up to a day for the free tier).
            match = re.search(r"retryDelay['\"]?:\s*['\"](\d+)", str(error))
            wait = int(match.group(1)) if match else config.GEMINI_BREAKER_COOLDOWN_S
            self._skip_until = time.monotonic() + wait
            self.gemini_note = f"Gemini-Kontingent erschöpft, wieder in {wait // 60} min"
        elif self._fails >= config.GEMINI_BREAKER_FAILS:
            self._skip_until = time.monotonic() + config.GEMINI_BREAKER_COOLDOWN_S
            self.gemini_note = f"Gemini-Fehler, Pause {config.GEMINI_BREAKER_COOLDOWN_S} s"
        print(f"Gemini failed ({type(error).__name__}: {str(error)[:300]}) - using Ollama")

    async def generate_json(self, system, prompt, schema=SUGGESTION_SCHEMA):
        text, engine, secs = await self.generate(system, prompt, schema)
        return parse_json(text), engine, secs

    def _gemini_config(self, system, schema):
        from google.genai import types

        kwargs = {"system_instruction": system, "temperature": 0.3}
        if schema:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_json_schema"] = schema
        if self._thinking and self._thinking != "off":
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=self._thinking.upper())
        return types.GenerateContentConfig(**kwargs)

    async def _gemini_call(self, system, prompt, schema):
        # Not streamed: in JSON mode the chunks arrive together at the end anyway.
        from google.genai import errors

        async def call():
            return await self._gemini.aio.models.generate_content(
                model=config.GEMINI_MODEL, contents=prompt, config=self._gemini_config(system, schema)
            )

        try:
            response = await asyncio.wait_for(call(), config.GEMINI_HARD_TIMEOUT_S)
        except errors.ClientError as e:
            # Some models reject a thinking level (3.8 Flash has no "minimal"): step down once.
            if self._thinking == "off" or "thinking" not in str(e).lower():
                raise
            self._thinking = "low" if self._thinking == "minimal" else "off"
            print(f"{config.GEMINI_MODEL}: thinking level not supported, using '{self._thinking}'")
            response = await asyncio.wait_for(call(), config.GEMINI_HARD_TIMEOUT_S)
        if not response.text:
            raise RuntimeError(f"empty Gemini response ({response.candidates and response.candidates[0].finish_reason})")
        return response.text

    async def _ollama_call(self, system, prompt, schema):
        body = {
            "model": config.OLLAMA_MODEL,
            "stream": False,
            "keep_alive": "30m",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "options": {"temperature": 0.3},
        }
        if schema:
            body["format"] = schema
        r = await self._http.post(f"{config.OLLAMA_URL}/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"]

    async def warmup(self):
        """Load the Ollama model into memory so the first fallback isn't a cold start."""
        try:
            await self._http.post(
                f"{config.OLLAMA_URL}/api/chat",
                json={"model": config.OLLAMA_MODEL, "messages": [], "keep_alive": "30m"},
            )
        except httpx.HTTPError as e:
            print(f"Ollama warmup failed: {e}")
