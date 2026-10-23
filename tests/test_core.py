import asyncio

import numpy as np

from copilot import config
from copilot.facts import Facts
from copilot.llm import LLM, parse_json
from copilot.rag import split_text
from copilot.stt import Segment, is_hallucination
from copilot.transcript import Transcript
from copilot.vad import Chunker

F = config.VAD_FRAME
FPS = config.SAMPLE_RATE / F


def feed(chunker, pattern):
    """pattern: list of (seconds, is_speech)."""
    out = []
    for secs, speech in pattern:
        for _ in range(int(secs * FPS)):
            frame = np.full(F, 0.1 if speech else 0.0, dtype=np.float32)
            out += chunker.feed(frame, 0.9 if speech else 0.0)
    return out


# --- VAD chunker ---------------------------------------------------------------
def test_long_speech_is_force_split_at_max():
    chunks = feed(Chunker("P"), [(6.0, True), (1.0, False)])
    assert len(chunks) >= 3
    assert all(c.duration <= config.CHUNK_MAX_S + 0.05 for c in chunks)


def test_short_utterance_flushed_after_longer_pause():
    chunks = feed(Chunker("P"), [(0.6, True), (1.0, False)])
    assert len(chunks) == 1 and chunks[0].duration < config.CHUNK_MIN_S


def test_brief_pause_in_short_chunk_does_not_split():
    # 0.8 s speech, 0.35 s pause (> SILENCE_END but chunk < CHUNK_MIN), more speech.
    chunks = feed(Chunker("P"), [(0.8, True), (0.35, False), (0.5, True), (1.0, False)])
    assert len(chunks) == 1


def test_pause_after_min_length_ends_chunk():
    chunks = feed(Chunker("P"), [(1.7, True), (0.4, False), (1.7, True), (1.0, False)])
    assert len(chunks) == 2
    assert all(config.CHUNK_MIN_S <= c.duration <= config.CHUNK_MAX_S for c in chunks)


def test_clicks_are_dropped():
    assert feed(Chunker("P"), [(0.1, True), (1.0, False)]) == []


# --- transcript ------------------------------------------------------------------
def seg(source, text, t_end, duration=2.0):
    return Segment(source, text, "de", duration, t_end)


def test_turn_merging_and_speaker_switch():
    tr = Transcript()
    _, new1, _ = tr.add(seg("Participant", "Hallo zusammen,", 10.0))
    _, new2, _ = tr.add(seg("Participant", "wie geht es?", 12.5))  # gap 0.5 s
    _, new3, _ = tr.add(seg("User", "Gut, danke.", 15.0))
    _, new4, _ = tr.add(seg("Participant", "Schön.", 30.0))
    assert (new1, new2, new3, new4) == (True, False, True, True)
    assert tr.render().splitlines()[0] == "[Participant] Hallo zusammen, wie geht es?"


def test_echo_mic_after_loopback_is_dropped():
    tr = Transcript()
    tr.add(seg("Participant", "Das Budget liegt bei fünfzigtausend Euro", 10.0))
    turn, _, _ = tr.add(seg("User", "das Budget liegt bei fünfzigtausend Euro", 10.4))
    assert turn is None and len(tr.turns) == 1


def test_echo_mic_before_loopback_is_retracted():
    tr = Transcript()
    tr.add(seg("User", "das Budget liegt bei fünfzigtausend Euro", 10.0))
    _, _, retracted = tr.add(seg("Participant", "Das Budget liegt bei fünfzigtausend Euro", 10.3))
    assert len(retracted) == 1
    assert [t.source for t in tr.turns] == ["Participant"]
    assert [s.source for s in tr.segments] == ["Participant"]


def test_short_user_reply_is_not_echo():
    tr = Transcript()
    tr.add(seg("Participant", "Passt das für Sie so, ja?", 10.0))
    turn, _, _ = tr.add(seg("User", "Ja.", 10.5))
    assert turn is not None


# --- facts -------------------------------------------------------------------------
def test_facts_union_and_replace(tmp_path):
    f = Facts(tmp_path / "facts.json")
    f.data = {"names": ["Sarah"], "Budget": "10k"}
    f.merge([{"key": "names", "value": "Tom"}, {"key": "budget", "value": "50,000 EUR"},
             {"key": "Deadline", "value": ""}])
    assert f.data == {"names": ["Sarah", "Tom"], "Budget": "50,000 EUR"}
    assert Facts(tmp_path / "facts.json").data == f.data


# --- llm -----------------------------------------------------------------------------
def test_parse_json_variants():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json("kein json") is None


def _llm(monkeypatch, gemini_delay, ollama_delay, gemini_error=None):
    monkeypatch.setattr(config, "GEMINI_TIMEOUT_S", 0.05)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    client = LLM()
    client._gemini = object()

    async def gemini(*a):
        await asyncio.sleep(gemini_delay)
        if gemini_error:
            raise gemini_error
        return '{"trigger": "G", "suggestions": [], "new_facts": []}'

    async def ollama(*a):
        await asyncio.sleep(ollama_delay)
        return '{"trigger": "O", "suggestions": [], "new_facts": []}'

    client._gemini_call = gemini
    client._ollama_call = ollama
    return client


def test_slow_gemini_still_wins_race_against_slower_ollama(monkeypatch):
    client = _llm(monkeypatch, gemini_delay=0.15, ollama_delay=0.5)
    res, engine, _ = asyncio.run(client.generate_json("s", "p"))
    assert engine == "gemini" and res["trigger"] == "G"
    assert client.gemini_available


def test_ollama_wins_when_gemini_is_much_slower(monkeypatch):
    client = _llm(monkeypatch, gemini_delay=1.0, ollama_delay=0.05)
    res, engine, _ = asyncio.run(client.generate_json("s", "p"))
    assert engine == "ollama" and res["trigger"] == "O"
    assert client.gemini_available  # slowness alone does not trip the breaker


def test_gemini_errors_fall_back_and_open_breaker(monkeypatch):
    client = _llm(monkeypatch, gemini_delay=0.0, ollama_delay=0.0, gemini_error=RuntimeError("503"))

    async def run():
        await client.generate_json("s", "p")
        return await client.generate_json("s", "p")

    res, engine, _ = asyncio.run(run())
    assert engine == "ollama" and res["trigger"] == "O"
    assert not client.gemini_available


def test_quota_429_skips_gemini_for_retry_delay(monkeypatch):
    err = RuntimeError("429 RESOURCE_EXHAUSTED {'retryDelay': '62535s'}")
    err.code = 429
    client = _llm(monkeypatch, gemini_delay=0.0, ollama_delay=0.0, gemini_error=err)
    _, engine, _ = asyncio.run(client.generate_json("s", "p"))
    assert engine == "ollama" and not client.gemini_available
    assert "1042 min" in client.gemini_note


# --- memory --------------------------------------------------------------------------
def test_memory_summarizes_only_new_segments(tmp_path):
    from copilot import memory
    from copilot.memory import Memory

    calls = []

    class FakeLLM:
        async def generate(self, system, prompt):
            calls.append(prompt)
            return f"- Zusammenfassung {len(calls)}", "gemini", 0.5

    class FakeAssistant:
        transcript = Transcript()
        memory = Memory(tmp_path / "s.memory.md")
        llm = FakeLLM()
        events = []
        emit = events.append

    a = FakeAssistant()
    a.transcript.add(seg("Participant", "Kurz.", 1.0))
    asyncio.run(memory.summarize(a))
    assert calls == []  # below MEMORY_MIN_WORDS

    a.transcript.add(seg("Participant", "Wir brauchen die Website bis Ende März " * 6, 5.0))
    asyncio.run(memory.summarize(a))
    assert "Ende März" in calls[0] and a.memory.text == "- Zusammenfassung 1"
    assert (tmp_path / "s.memory.md").read_text(encoding="utf-8") == "- Zusammenfassung 1"

    a.transcript.add(seg("User", "Das bekommen wir hin, versprochen " * 8, 30.0))
    asyncio.run(memory.summarize(a))
    assert "Ende März" not in calls[1].split("[NEW TRANSCRIPT]")[1]  # only the new part
    assert "- Zusammenfassung 1" in calls[1]  # previous memory is carried forward
    assert a.events[-1]["type"] == "memory"


def test_kb_sources_include_extra_files_and_folders(tmp_path, monkeypatch):
    from copilot import rag

    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "a.md").write_text("x")
    (tmp_path / "kb" / "skip.txt").write_text("x")
    (tmp_path / "prep.md").write_text("x")
    (tmp_path / "vault" / "sub").mkdir(parents=True)
    (tmp_path / "vault" / "sub" / "b.md").write_text("x")
    monkeypatch.setattr(config, "KB_DIR", tmp_path / "kb")
    monkeypatch.setattr(config, "KB_EXTRA_PATHS", [tmp_path / "prep.md", tmp_path / "vault"])
    assert [p.name for p in rag._sources()] == ["a.md", "prep.md", "b.md"]


# --- misc ----------------------------------------------------------------------------
def test_hallucination_filter():
    assert is_hallucination("Untertitel im Auftrag des ZDF", 0.5, -0.5)
    assert is_hallucination("  ...  ", 0.0, 0.0)
    assert not is_hallucination("Was kostet das?", 0.1, -0.3)


def test_split_text_respects_size():
    text = "\n\n".join(f"Absatz {i}. " + "Wort " * 60 for i in range(10))
    chunks = split_text(text)
    assert len(chunks) > 1 and all(len(c) <= 500 for c in chunks)
