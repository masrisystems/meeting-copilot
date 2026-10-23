"""Rolling meeting memory: every MEMORY_INTERVAL_S the new part of the transcript is
folded into a compact summary, so the LLM still knows what happened beyond the last
MAX_TURNS turns."""

import asyncio

from . import config

MEMORY_SYSTEM = """You maintain the running memory of a live meeting for an AI meeting advisor.
You get the current memory and the new transcript lines since the last update.
Return the updated memory in German as short bullet points, grouped under these headings:
## Verlauf (what was discussed, in order)
## Entscheidungen & Zusagen
## Offene Punkte & Einwände
## Stimmung & Prioritäten der Teilnehmer
Keep concrete numbers, names, dates and commitments. Drop small talk. Max ~250 words.
Output only the memory, no preamble."""


class Memory:
    def __init__(self, path=None):
        self.text = ""
        self.path = path  # written next to the session log
        self._cursor = 0  # id of the last segment already in the memory

    def pending(self, segments):
        return [s for s in segments if s.id > self._cursor]

    def prompt(self, segments):
        lines = "\n".join(f"[{s.source}] {s.text}" for s in segments)
        return f"[CURRENT MEMORY]\n{self.text or '(leer)'}\n\n[NEW TRANSCRIPT]\n{lines}"

    def update(self, text, segments):
        self.text = text.strip()
        self._cursor = segments[-1].id
        if self.path:
            self.path.write_text(self.text, encoding="utf-8")

    def render(self):
        return self.text or "(noch keine)"


async def run(assistant, interval=config.MEMORY_INTERVAL_S):
    """Background loop started by __main__."""
    while True:
        await asyncio.sleep(interval)
        await summarize(assistant)


async def summarize(assistant):
    memory, transcript = assistant.memory, assistant.transcript
    new = memory.pending(transcript.segments)
    if sum(len(s.text.split()) for s in new) < config.MEMORY_MIN_WORDS:
        return
    try:
        text, engine, secs = await assistant.llm.generate(MEMORY_SYSTEM, memory.prompt(new))
    except Exception as e:
        assistant.emit({"type": "error", "text": f"Memory: {e}"})
        return
    memory.update(text, new)
    assistant.emit({"type": "memory", "text": memory.text, "engine": engine, "ms": round(secs * 1000)})
