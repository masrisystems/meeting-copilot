"""Primary cloud LLM (selectable: Gemini, Claude, OpenAI, Grok, any OpenAI-compatible URL)
with local Ollama as fallback.

If the primary hasn't answered within CLOUD_TIMEOUT_S, Ollama is started in parallel and
the first finished answer wins. After repeated errors (or a quota error) a breaker skips
the primary for a cooldown so offline mode costs no extra latency.
"""

import asyncio
import json
import re
import time

from . import config
from .providers import build_providers


def _closed(schema):
    """Add additionalProperties: false to every object (required by Claude and OpenAI strict mode)."""
    if isinstance(schema, dict):
        schema = {k: _closed(v) for k, v in schema.items()}
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
    return schema


SUGGESTION_SCHEMA = _closed({
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
})


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
    def __init__(self, providers=None):
        self.providers = {p.id: p for p in (providers or build_providers())}
        self.fallback = self.providers["ollama"]
        self._fails = 0
        self._skip_until = 0.0
        self.note = ""  # why the primary is currently skipped, shown in the UI
        self.ollama_models = []  # installed, refreshed by check_ollama()
        self.ollama_note = ""  # Ollama not running / model missing, shown in the UI
        self.primary = self.fallback
        self.set_primary(config.LLM_PROVIDER)

    def set_primary(self, provider_id):
        """Switch the cloud engine at runtime. Unknown or unconfigured ids fall back to Ollama.
        "ollama:<model>" also switches the Ollama model (used as the fallback as well)."""
        provider_id, _, model = provider_id.partition(":")
        if provider_id == "ollama" and model:
            self.fallback.model = model
            self._update_ollama_note()
        p = self.providers.get(provider_id)
        if p is None or not p.configured:
            print(f"LLM provider '{provider_id}' is not configured (missing key/URL/model) - using Ollama only.")
            p = self.fallback
            self.note = f"{provider_id}: kein API-Key/URL" if provider_id != "ollama" else ""
        else:
            self.note = ""
        self.primary = p
        self._fails = 0
        self._skip_until = 0.0

    def describe(self):
        """Provider list for the UI selector."""
        return {"type": "providers", "active": self.primary.id,
                "list": [{"id": p.id, "label": p.label, "model": p.model, "configured": p.configured}
                         for p in self.providers.values()],
                "ollama_models": self.ollama_models, "ollama_note": self.ollama_note}

    async def check_ollama(self):
        """Refresh the installed Ollama models. Returns True when the UI needs an update."""
        before = (self.ollama_models, self.ollama_note)
        models = await self.fallback.installed()
        self.ollama_models = models or []
        self._update_ollama_note(running=models is not None)
        return (self.ollama_models, self.ollama_note) != before

    def _update_ollama_note(self, running=True):
        m = self.fallback.model
        if not running:
            self.ollama_note = "Ollama läuft nicht (ollama.com): kein lokaler Fallback"
        elif self.ollama_models and m not in self.ollama_models:
            self.ollama_note = f"Ollama-Modell {m} fehlt: ollama pull {m}, oder oben ein installiertes wählen"
        elif not self.ollama_models:
            self.ollama_note = f"Kein Ollama-Modell installiert: ollama pull {m}"
        else:
            self.ollama_note = ""

    @property
    def primary_available(self):
        return self.primary is not self.fallback and time.monotonic() >= self._skip_until

    def label(self, engine):
        return self.providers[engine].label

    async def generate(self, system, prompt, schema=None):
        """Returns (text, engine_id, seconds). schema set -> JSON mode.

        The primary gets CLOUD_TIMEOUT_S on its own. If it hasn't answered by then, Ollama
        starts in parallel and whichever finishes first wins, so a slightly slow cloud
        answer is not thrown away. Only errors (not slowness) trip the breaker.
        """
        start = time.monotonic()
        if not self.primary_available:
            return await self.fallback.complete(system, prompt, schema), self.fallback.id, time.monotonic() - start

        primary = self.primary
        tasks = {asyncio.ensure_future(primary.complete(system, prompt, schema)): primary}
        done, _ = await asyncio.wait(tasks, timeout=config.CLOUD_TIMEOUT_S)
        if not done:
            tasks[asyncio.ensure_future(self.fallback.complete(system, prompt, schema))] = self.fallback

        error = None
        while tasks:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                provider = tasks.pop(task)
                if task.exception() is None:
                    for other in tasks:
                        other.cancel()
                    if provider is primary:
                        self._fails = 0
                        self.note = ""
                    return task.result(), provider.id, time.monotonic() - start
                error = task.exception()
                if provider is primary:
                    self._primary_failed(primary, error)
                    if not tasks:  # failed before the hedge started
                        tasks[asyncio.ensure_future(self.fallback.complete(system, prompt, schema))] = self.fallback
        raise error

    def _primary_failed(self, provider, error):
        if provider is not self.primary:  # switched while the request was running
            return
        self._fails += 1
        wait = provider.retry_after(error)
        if wait is not None:
            self._skip_until = time.monotonic() + wait
            self.note = f"{provider.label}: Limit/Kontingent erreicht, wieder in {max(1, wait // 60)} min"
        elif self._fails >= config.BREAKER_FAILS:
            self._skip_until = time.monotonic() + config.BREAKER_COOLDOWN_S
            self.note = f"{provider.label}-Fehler, Pause {config.BREAKER_COOLDOWN_S} s"
        print(f"{provider.label} failed ({type(error).__name__}: {str(error)[:300]}) - using Ollama")

    async def generate_json(self, system, prompt, schema=SUGGESTION_SCHEMA):
        text, engine, secs = await self.generate(system, prompt, schema)
        return parse_json(text), engine, secs

    async def warmup(self):
        await self.fallback.warmup()
