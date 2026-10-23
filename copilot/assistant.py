"""Orchestration: transcript in, proactive suggestions / push-to-ask answers / translations out.

All methods run on the asyncio loop. Segments arrive from the STT thread via
loop.call_soon_threadsafe(assistant.on_segment, seg).
"""

import asyncio
import time

from . import config
from .audio import PARTICIPANT

ASK_ADDENDUM = """
# Push-to-Ask
The user has asked you an explicit question (see [USER QUESTION]). Answer exactly that question,
grounded in the transcript, facts and knowledge. Keep the same JSON format: put your answer(s) in
"suggestions" (1-2 items, each 1-2 sentences, in German unless the question is in English) and set
"trigger" to a short label for the question.
"""

TRANSLATE_SYSTEM = (
    "You are a live interpreter. Translate the user's text into {target}. "
    "Output only the translation, no quotes, no comments."
)


class Assistant:
    def __init__(self, transcript, facts, kb, llm, emit, memory):
        self.transcript = transcript
        self.memory = memory
        self.facts = facts
        self.kb = kb
        self.llm = llm
        self.emit = emit  # callable(dict), must be called on the loop
        self.system = config.SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
        self.proactive = True
        self.translate = False
        self.engine = "-"
        self._timer = None
        self._pending_since = None
        self._running = False
        self._again = False

    # --- input ---------------------------------------------------------------
    def on_segment(self, seg):
        turn, new_turn, retracted = self.transcript.add(seg)
        for rid in retracted:
            self.emit({"type": "retract", "id": rid})
        if turn is None:
            return
        self.emit({"type": "segment", "id": seg.id, "source": seg.source, "text": seg.text,
                   "lang": seg.lang, "new_turn": new_turn, "stt_ms": round(seg.latency * 1000)})
        if seg.source != PARTICIPANT:
            return
        if self.translate:
            asyncio.ensure_future(self._translate(seg))
        if self.proactive:
            self._schedule()

    def _schedule(self):
        """Debounce on participant speech, but never hold text longer than PROACTIVE_MAX_PENDING_S."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        self._pending_since = self._pending_since or now
        delay = min(config.PROACTIVE_DEBOUNCE_S,
                    max(0.0, self._pending_since + config.PROACTIVE_MAX_PENDING_S - now))
        if self._timer:
            self._timer.cancel()
        self._timer = loop.call_later(delay, self._fire)

    def _fire(self):
        self._timer = None
        self._pending_since = None
        if self._running:
            self._again = True  # run once more with the newest transcript when this one ends
            return
        asyncio.ensure_future(self._proactive_loop())

    # --- proactive -----------------------------------------------------------
    async def _proactive_loop(self):
        self._running = True
        try:
            while True:
                self._again = False
                await self._run_proactive()
                if not self._again or not self.proactive:
                    break
        finally:
            self._running = False

    def _last_participant_text(self, max_chars=300):
        """Tail of the latest participant turn (a monologue becomes one long turn)."""
        for t in reversed(self.transcript.turns):
            if t.source == PARTICIPANT:
                return t.text[-max_chars:]
        return ""

    async def _context(self, rag_query):
        knowledge = await asyncio.to_thread(self.kb.context, rag_query)
        return (f"[FACTS]\n{self.facts.render()}\n\n[MEMORY]\n{self.memory.render()}\n\n"
                f"[KNOWLEDGE]\n{knowledge}\n\n[TRANSCRIPT]\n{self.transcript.render()}")

    async def _run_proactive(self):
        latest = self._last_participant_text()
        if not latest:
            return
        prompt = await self._context(latest) + "\n\nReagiere auf den letzten [Participant]-Beitrag."
        try:
            result, engine, secs = await self.llm.generate_json(self.system, prompt)
        except Exception as e:
            self.emit({"type": "error", "text": f"LLM: {e}"})
            return
        self._engine(engine, secs)
        if not isinstance(result, dict):
            return
        self._merge_facts(result)
        suggestions = [s for s in result.get("suggestions") or [] if s.get("text")]
        trigger = result.get("trigger") or "None"
        if suggestions and trigger.strip().lower() != "none":
            self.emit({"type": "suggestion", "trigger": trigger, "suggestions": suggestions[:2],
                       "engine": engine, "ms": round(secs * 1000), "ts": time.time()})

    # --- push-to-ask -----------------------------------------------------------
    async def ask(self, query):
        query = query.strip()
        if not query:
            return
        self.emit({"type": "asking", "query": query})
        prompt = (await self._context(f"{query} {self._last_participant_text()}")
                  + f"\n\n[USER QUESTION]\n{query}")
        try:
            result, engine, secs = await self.llm.generate_json(self.system + ASK_ADDENDUM, prompt)
        except Exception as e:
            self.emit({"type": "error", "text": f"LLM: {e}"})
            return
        self._engine(engine, secs)
        if not isinstance(result, dict):
            self.emit({"type": "error", "text": "Antwort war kein gültiges JSON."})
            return
        self._merge_facts(result)
        self.emit({"type": "answer", "query": query, "trigger": result.get("trigger", ""),
                   "suggestions": (result.get("suggestions") or [])[:2],
                   "engine": engine, "ms": round(secs * 1000), "ts": time.time()})

    # --- translation -----------------------------------------------------------
    async def _translate(self, seg):
        target = "English" if seg.lang == "de" else "German"
        try:
            text, engine, secs = await self.llm.generate(TRANSLATE_SYSTEM.format(target=target), seg.text)
        except Exception as e:
            self.emit({"type": "error", "text": f"Translation: {e}"})
            return
        self.emit({"type": "translation", "id": seg.id, "source": seg.source, "text": text.strip(),
                   "target": target[:2].upper(), "engine": engine, "ms": round(secs * 1000)})

    # --- toggles / helpers ---------------------------------------------------
    def set_mode(self, proactive=None, translate=None):
        if proactive is not None:
            self.proactive = proactive
        if translate is not None:
            self.translate = translate
        self.emit({"type": "mode", "proactive": self.proactive, "translate": self.translate})

    def _engine(self, engine, secs):
        self.engine = engine
        self.emit({"type": "engine", "engine": engine, "ms": round(secs * 1000)})

    def _merge_facts(self, result):
        new = result.get("new_facts")
        if isinstance(new, list) and self.facts.merge(new):
            self.emit({"type": "facts", "text": self.facts.render()})
