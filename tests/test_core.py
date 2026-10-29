import asyncio
from types import SimpleNamespace

import numpy as np

from copilot import config
from copilot.facts import Facts
import httpx

from copilot.llm import LLM, SUGGESTION_SCHEMA, parse_json
from copilot.providers import GeminiProvider, Provider
from copilot.rag import split_text
from copilot.hotkeys import to_pynput
from copilot.stt import Segment, is_hallucination, whisper_settings
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


class FakeProvider(Provider):
    def __init__(self, id, delay=0.0, error=None, configured=True):
        self.id, self.label, self.model = id, id.title(), f"{id}-model"
        self.delay, self.error, self._configured = delay, error, configured

    @property
    def configured(self):
        return self._configured

    async def complete(self, system, prompt, schema=None):
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return '{"trigger": "%s", "suggestions": [], "new_facts": []}' % self.id


def _llm(monkeypatch, cloud_delay, ollama_delay, cloud_error=None, cloud=None):
    monkeypatch.setattr(config, "CLOUD_TIMEOUT_S", 0.05)
    monkeypatch.setattr(config, "LLM_PROVIDER", "claude")
    cloud = cloud or GeminiProvider()  # real retry_after parsing for Gemini quota errors
    fake = FakeProvider("claude", cloud_delay, cloud_error)
    fake.retry_after = cloud.retry_after
    return LLM([fake, FakeProvider("grok", configured=False), FakeProvider("ollama", ollama_delay)])


def test_slow_cloud_still_wins_race_against_slower_ollama(monkeypatch):
    client = _llm(monkeypatch, cloud_delay=0.15, ollama_delay=0.5)
    res, engine, _ = asyncio.run(client.generate_json("s", "p"))
    assert engine == "claude" and res["trigger"] == "claude"
    assert client.primary_available


def test_ollama_wins_when_cloud_is_much_slower(monkeypatch):
    client = _llm(monkeypatch, cloud_delay=1.0, ollama_delay=0.05)
    res, engine, _ = asyncio.run(client.generate_json("s", "p"))
    assert engine == "ollama" and res["trigger"] == "ollama"
    assert client.primary_available  # slowness alone does not trip the breaker


def test_cloud_errors_fall_back_and_open_breaker(monkeypatch):
    client = _llm(monkeypatch, cloud_delay=0.0, ollama_delay=0.0, cloud_error=RuntimeError("503"))

    async def run():
        await client.generate_json("s", "p")
        return await client.generate_json("s", "p")

    res, engine, _ = asyncio.run(run())
    assert engine == "ollama" and res["trigger"] == "ollama"
    assert not client.primary_available


def test_gemini_quota_429_skips_primary_for_retry_delay(monkeypatch):
    err = RuntimeError("429 RESOURCE_EXHAUSTED {'retryDelay': '62535s'}")
    err.code = 429
    client = _llm(monkeypatch, cloud_delay=0.0, ollama_delay=0.0, cloud_error=err)
    _, engine, _ = asyncio.run(client.generate_json("s", "p"))
    assert engine == "ollama" and not client.primary_available
    assert "1042 min" in client.note


def test_http_429_uses_retry_after_header():
    class Resp:
        headers = {"retry-after": "30"}

    err = RuntimeError("rate limited")
    err.status_code, err.response = 429, Resp()
    assert Provider().retry_after(err) == 30
    assert Provider().retry_after(RuntimeError("boom")) is None


def test_switching_provider(monkeypatch):
    client = _llm(monkeypatch, cloud_delay=0.0, ollama_delay=0.0)
    assert client.primary.id == "claude"
    client.set_primary("grok")  # no key -> stays usable via Ollama, with a note
    assert client.primary.id == "ollama" and not client.primary_available and "grok" in client.note
    client.set_primary("claude")
    assert client.primary.id == "claude" and client.note == ""
    listed = client.describe()
    assert listed["active"] == "claude"
    assert [p["configured"] for p in listed["list"]] == [True, False, True]


def test_openai_compatible_downgrades_json_mode():
    import openai

    from copilot.providers import OpenAICompatProvider

    p = OpenAICompatProvider("custom", "OmniRoute", "http://localhost:9/v1", "", "gh/gpt-x", needs_key=False)
    calls = []

    class Choice:
        finish_reason = "stop"

        class message:
            content = '{"trigger": "ok", "suggestions": [], "new_facts": []}'

    class Completions:
        async def create(self, **kw):
            calls.append(kw.get("response_format", {}).get("type"))
            if kw.get("response_format"):
                raise openai.BadRequestError("response_format json_schema not supported",
                                             response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
                                             body=None)
            return type("R", (), {"choices": [Choice]})()

    p._client = type("C", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    assert parse_json(asyncio.run(p.complete("s", "p", SUGGESTION_SCHEMA)))["trigger"] == "ok"
    assert calls == ["json_schema", "json_object", None] and p._json_mode == "prompt"


def test_schema_is_closed_for_strict_providers():
    assert SUGGESTION_SCHEMA["additionalProperties"] is False
    assert SUGGESTION_SCHEMA["properties"]["suggestions"]["items"]["additionalProperties"] is False


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
    assert is_hallucination("Untertitel im Auftrag des ZDF für funk, 2017", 0.0, -0.2)
    assert is_hallucination("  ...  ", 0.0, 0.0)
    assert not is_hallucination("Was kostet das?", 0.1, -0.3)


def test_split_text_respects_size():
    text = "\n\n".join(f"Absatz {i}. " + "Wort " * 60 for i in range(10))
    chunks = split_text(text)
    assert len(chunks) > 1 and all(len(c) <= 500 for c in chunks)


# --- platform ------------------------------------------------------------------------
def test_whisper_auto_picks_gpu_or_cpu_defaults(monkeypatch):
    for name in ("WHISPER_DEVICE", "WHISPER_MODEL", "WHISPER_COMPUTE"):
        monkeypatch.setattr(config, name, "auto")
    assert whisper_settings(1) == ("large-v3-turbo", "cuda", "float16")
    assert whisper_settings(0) == ("small", "cpu", "int8")
    monkeypatch.setattr(config, "WHISPER_MODEL", "base")
    monkeypatch.setattr(config, "WHISPER_DEVICE", "cpu")
    assert whisper_settings(1) == ("base", "cpu", "int8")


def test_hotkeys_convert_to_pynput_format():
    assert to_pynput("ctrl+alt+1") == "<ctrl>+<alt>+1"
    assert to_pynput("ctrl+alt+space") == "<ctrl>+<alt>+<space>"
    assert all(to_pynput(p["hotkey"]) for p in config.PRESETS)


# --- ollama models -------------------------------------------------------------------
def test_ollama_model_switch_and_status_note(monkeypatch):
    client = _llm(monkeypatch, cloud_delay=0, ollama_delay=0)
    ollama = client.fallback
    installed = None

    async def fake_installed():
        return installed

    ollama.installed = fake_installed
    assert asyncio.run(client.check_ollama()) and "läuft nicht" in client.ollama_note

    installed = ["llama3.2:3b", "qwen3:4b"]
    assert asyncio.run(client.check_ollama()) and "ollama-model fehlt" in client.ollama_note
    assert not asyncio.run(client.check_ollama())  # unchanged: no UI update

    client.set_primary("ollama:qwen3:4b")
    assert client.primary is ollama and ollama.model == "qwen3:4b" and client.ollama_note == ""
    assert client.describe()["ollama_models"] == installed


# --- knowledge base sync -------------------------------------------------------------
def test_kb_sync_reindexes_only_on_change_and_drops_stale_index(tmp_path, monkeypatch):
    import lancedb

    from copilot import rag

    monkeypatch.setattr(config, "KB_DIR", tmp_path / "kb")
    monkeypatch.setattr(config, "KB_EXTRA_PATHS", [])
    monkeypatch.setattr(config, "DB_DIR", tmp_path / "db")
    monkeypatch.setattr(rag, "_embed", lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "preise.md").write_text("Eine Website kostet ab 999 Euro.", encoding="utf-8")
    calls = []
    real_index = rag.index
    monkeypatch.setattr(rag, "index", lambda: (calls.append(1), real_index()))

    assert rag.sync() == 1 and len(calls) == 1
    assert rag.sync() == 1 and len(calls) == 1  # nothing changed
    (tmp_path / "kb" / "preise.md").unlink()
    assert rag.sync() == 0 and len(calls) == 2
    assert config.DB_TABLE not in lancedb.connect(str(tmp_path / "db")).list_tables().tables


# --- meeting -------------------------------------------------------------------------
class FakeSource:
    def __init__(self):
        self.open = False

    def start(self):
        self.open = True

    def stop(self):
        self.open = False


def _meeting_assistant(tmp_path, monkeypatch, open_capture):
    from copilot.assistant import Assistant
    from copilot.memory import Memory

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    events = []
    a = Assistant(Transcript(), Facts(tmp_path / "facts.json"), None, None, events.append, Memory(), open_capture)
    return a, events


def test_meeting_opens_devices_only_while_running_and_mute_keeps_participants(tmp_path, monkeypatch):
    class FakeCapture:
        mic, loopback = FakeSource(), FakeSource()
        sources = [mic, loopback]

    cap = FakeCapture()
    a, events = _meeting_assistant(tmp_path, monkeypatch, lambda: cap)

    async def run():
        assert a.capture is None  # idle: no device touched
        a.start_meeting()
        assert cap.mic.open and cap.loopback.open and a.transcript.session_path.parent == tmp_path
        assert {"type": "reset"} in events
        a.set_mic(False)
        assert not cap.mic.open and cap.loopback.open
        a.stop_meeting()
        assert not cap.mic.open and not cap.loopback.open
        a.start_meeting()  # mute is remembered
        assert not cap.mic.open and cap.loopback.open
        await asyncio.sleep(0)

    asyncio.run(run())
    assert events[-1] == {"type": "meeting", "active": True, "mic": False, "error": ""}


def test_meeting_audio_error_is_reported_not_fatal(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("'parec' not found")

    a, events = _meeting_assistant(tmp_path, monkeypatch, broken)
    a.start_meeting()
    assert events[-1] == {"type": "meeting", "active": False, "mic": True, "error": "'parec' not found"}


def test_meeting_record_is_saved_on_stop(tmp_path, monkeypatch):
    cap = SimpleNamespace(mic=FakeSource(), loopback=FakeSource())
    cap.sources = [cap.mic, cap.loopback]
    a, events = _meeting_assistant(tmp_path, monkeypatch, lambda: cap)
    monkeypatch.setattr(config, "MEETINGS_DIR", tmp_path / "meetings")

    async def run():
        a.start_meeting()
        a.on_segment(seg("Participant", "Was kostet die Website?", 1.0))
        a.on_segment(seg("User", "Ab 999 Euro.", 3.0))
        a._card({"type": "suggestion", "trigger": "Preisfrage", "ts": 0,
                 "suggestions": [{"text": "Das hängt vom Umfang ab."}]})
        await a.stop_meeting()

    asyncio.run(run())
    [record] = (tmp_path / "meetings").iterdir()
    text = record.read_text(encoding="utf-8")
    assert "**Teilnehmer:** Was kostet die Website?" in text and "**Du:** Ab 999 Euro." in text
    assert "Preisfrage" in text and "Das hängt vom Umfang ab." in text
    assert events[-1] == {"type": "saved", "path": str(record)}


def test_empty_meeting_writes_no_record(tmp_path, monkeypatch):
    cap = SimpleNamespace(mic=FakeSource(), loopback=FakeSource())
    cap.sources = [cap.mic, cap.loopback]
    a, _ = _meeting_assistant(tmp_path, monkeypatch, lambda: cap)
    monkeypatch.setattr(config, "MEETINGS_DIR", tmp_path / "meetings")

    async def run():
        a.start_meeting()
        await a.stop_meeting()

    asyncio.run(run())
    assert not (tmp_path / "meetings").exists()


# --- languages -----------------------------------------------------------------------
def test_translation_goes_into_your_language_and_from_it_into_the_second(tmp_path, monkeypatch):
    targets = []

    class FakeLLM:
        async def generate(self, system, prompt):
            targets.append(system.split("'")[-2])
            return "x", "ollama", 0.1

    a, _ = _meeting_assistant(tmp_path, monkeypatch, None)
    a.llm = FakeLLM()
    monkeypatch.setattr(config, "LANGUAGES", ["ar", "de", "en"])
    for lang in ("tr", "ar"):
        s = seg("Participant", "Merhaba", 1.0)
        s.lang = lang
        asyncio.run(a._translate(s))
    assert targets == ["ar", "de"]


# --- window --------------------------------------------------------------------------
def test_window_settings_are_clamped_and_persisted(tmp_path, monkeypatch):
    from copilot import window

    monkeypatch.setattr(window, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(window, "supported", lambda: False)
    w = window.Window()
    assert w.state() == {"type": "window", "supported": False, "on_top": True, "opacity": 1.0}
    w.set(on_top=False, opacity=0.1)
    assert window.Window().settings == {"on_top": False, "opacity": 0.4}


# --- proactive pacing ----------------------------------------------------------------
def _proactive_assistant(tmp_path, monkeypatch, triggers):
    a, events = _meeting_assistant(tmp_path, monkeypatch, None)
    answers = iter(triggers)

    class FakeLLM:
        async def generate_json(self, system, prompt):
            t = next(answers)
            return {"trigger": t, "suggestions": [{"text": f"{t} 1"}, {"text": f"{t} 2"}], "new_facts": []}, "ollama", 0.1

    a.llm = FakeLLM()
    a.kb = SimpleNamespace(context=lambda q: "")
    a.transcript.add(seg("Participant", "Das ist uns zu teuer.", 1.0))
    return a, events


def test_proactive_skips_the_same_trigger_but_shows_a_new_one(tmp_path, monkeypatch):
    a, events = _proactive_assistant(tmp_path, monkeypatch, ["Preiseinwand", "preiseinwand", "Terminfrage"])
    for _ in range(3):
        asyncio.run(a._run_proactive())
    assert [e["trigger"] for e in events if e["type"] == "suggestion"] == ["Preiseinwand", "Terminfrage"]


def test_single_suggestion_option(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SINGLE_SUGGESTION", True)
    a, events = _proactive_assistant(tmp_path, monkeypatch, ["Preiseinwand"])
    asyncio.run(a._run_proactive())
    [card] = [e for e in events if e["type"] == "suggestion"]
    assert [s["text"] for s in card["suggestions"]] == ["Preiseinwand 1"]


def test_proactive_waits_for_the_end_of_the_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROACTIVE_DEBOUNCE_S", 0.2)
    monkeypatch.setattr(config, "PROACTIVE_MAX_PENDING_S", 5)
    a, _ = _meeting_assistant(tmp_path, monkeypatch, None)
    fired = []
    a._fire = lambda: fired.append(True)

    async def run():
        for _ in range(5):  # the participant keeps talking: one segment every 0.1 s
            a._schedule()
            await asyncio.sleep(0.1)
        assert fired == []
        await asyncio.sleep(0.25)  # pause after the turn
        assert fired == [True]

    asyncio.run(run())
