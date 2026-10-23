"""Turn buffer, echo suppression and the JSONL session log."""

import itertools
import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field

from rapidfuzz import fuzz

from . import config
from .audio import PARTICIPANT, USER

_ids = itertools.count(1)


@dataclass
class Turn:
    source: str
    segments: list = field(default_factory=list)

    @property
    def text(self):
        return " ".join(s.text for s in self.segments)

    @property
    def lang(self):
        return self.segments[-1].lang if self.segments else "de"

    @property
    def t_end(self):
        return self.segments[-1].t_end


def _is_echo(a, b):
    """True when b (mic) is most likely a pickup of a (loopback), or vice versa."""
    short, long_ = sorted((a, b), key=len)
    if len(short) < 12:
        return False
    return fuzz.partial_ratio(short.lower(), long_.lower()) >= config.ECHO_SIMILARITY


class Transcript:
    def __init__(self, session_path=None):
        self.turns = deque(maxlen=config.MAX_TURNS)
        self.segments = []  # whole meeting, read by the memory summarizer
        self._recent = deque(maxlen=50)  # (segment, turn) for echo matching
        self.session_path = session_path
        if session_path:
            session_path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, seg):
        """Returns (turn, new_turn, retracted_ids); turn is None when seg was dropped as echo."""
        seg.id = next(_ids)
        window = [(s, t) for s, t in self._recent if abs(s.t_end - seg.t_end) <= config.ECHO_WINDOW_S]
        retracted = []

        if seg.source == USER:
            if any(s.source == PARTICIPANT and _is_echo(s.text, seg.text) for s, _ in window):
                return None, False, []
        else:
            # The mic copy may have been transcribed first; pull it back out.
            for s, t in window:
                if s.source == USER and _is_echo(seg.text, s.text):
                    t.segments.remove(s)
                    if not t.segments and t in self.turns:
                        self.turns.remove(t)
                    self._recent.remove((s, t))
                    self.segments.remove(s)
                    retracted.append(s.id)

        last = self.turns[-1] if self.turns else None
        gap = (seg.t_end - seg.duration) - last.t_end if last else None
        if last and last.source == seg.source and gap <= config.TURN_GAP_S:
            last.segments.append(seg)
            turn, new = last, False
        else:
            turn, new = Turn(seg.source, [seg]), True
            self.turns.append(turn)

        self._recent.append((seg, turn))
        self.segments.append(seg)
        self._log(seg)
        return turn, new, retracted

    def render(self, n=config.MAX_TURNS):
        return "\n".join(f"[{t.source}] {t.text}" for t in list(self.turns)[-n:])

    def _log(self, seg):
        if not self.session_path:
            return
        rec = {k: v for k, v in asdict(seg).items() if k != "t_end"}
        with open(self.session_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def new_session_path():
    return config.SESSIONS_DIR / (time.strftime("%Y-%m-%d_%H-%M-%S") + ".jsonl")
