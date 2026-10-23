"""Persistent facts sheet (facts.json): load, merge LLM-extracted facts, render for the prompt."""

import json
import os
import tempfile

from . import config


class Facts:
    def __init__(self, path=config.FACTS_FILE, fresh=False):
        self.path = path
        self.data = {}
        if not fresh and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(f"Could not load {path}: {e}")

    def _key(self, key):
        for k in self.data:
            if k.lower() == key.lower():
                return k
        return key

    def merge(self, new_facts):
        """Apply [{key, value}] items. Lists are unioned, scalars replaced. Returns True if changed."""
        changed = False
        for item in new_facts or []:
            key = str(item.get("key", "")).strip()
            value = item.get("value")
            if isinstance(value, str):
                value = value.strip()
            if not key or value in (None, "", []):
                continue
            key = self._key(key)
            old = self.data.get(key)
            if isinstance(old, list):
                for v in value if isinstance(value, list) else [value]:
                    if v not in old:
                        old.append(v)
                        changed = True
            elif old != value:
                self.data[key] = value
                changed = True
        if changed:
            self.save()
        return changed

    def save(self):
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def render(self):
        lines = []
        for k, v in self.data.items():
            if v in ("", [], None):
                continue
            lines.append(f"- {k}: {', '.join(map(str, v)) if isinstance(v, list) else v}")
        return "\n".join(lines) or "(noch keine)"
