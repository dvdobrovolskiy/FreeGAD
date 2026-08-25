# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""Persistent chat transcripts, one JSON file per document in %APPDATA%\\FreeGAD\\history\\<key>.json.

What is stored is the *displayed* transcript (user / assistant / system lines with timestamps) -
not the raw API history with tool results and screenshots, which would be huge and is rebuilt
fresh from the live document anyway. The tail of the transcript is shown again when the panel
opens and a short excerpt is given to Claude as "recent conversation" context.

Recycling so it never grows unbounded:
  - per file:   at most MAX_ENTRIES entries / MAX_BYTES bytes (oldest dropped, each text capped)
  - per folder: files untouched for MAX_AGE_DAYS are deleted, and if the folder exceeds
                MAX_TOTAL_BYTES the oldest files go first (run once per FreeCAD session).
"""
import datetime
import json
import os
import time

from . import config, memory

MAX_ENTRIES = 400
MAX_BYTES = 400 * 1024
MAX_TEXT = 20000
MAX_AGE_DAYS = 180
MAX_TOTAL_BYTES = 25 * 1024 * 1024

SHOW_ON_OPEN = 40            # transcript lines restored into the panel
PROMPT_ENTRIES = 16          # lines handed to Claude as context
PROMPT_TEXT = 600            # chars per line in that excerpt


def history_dir():
    return os.path.join(config.app_dir(), "history")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


class History:
    def __init__(self, doc):
        self.key = memory.document_key(doc) if doc is not None else "nodoc"
        try:
            self.title = os.path.basename(doc.FileName) if (doc is not None and doc.FileName) else (doc.Label if doc else "(no document)")
        except Exception:
            self.title = "(document)"
        self.path = os.path.join(history_dir(), self.key + ".json")
        self.entries = []
        self._load()

    def _load(self):
        try:
            if not os.path.exists(self.path):
                return
            with open(self.path, "r", encoding="utf-8") as f:
                root = json.load(f)
            for e in root.get("entries", []):
                if isinstance(e, dict) and e.get("role") and e.get("text"):
                    self.entries.append({"role": e["role"], "text": e["text"], "ts": e.get("ts") or ""})
        except Exception:
            self.entries = []

    def append(self, role, text):
        text = (text or "").strip()
        if not text:
            return
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "\n…(truncated)"
        self.entries.append({"role": role, "text": text, "ts": _now()})
        self._trim()
        self.save()

    def _trim(self):
        if len(self.entries) > MAX_ENTRIES:
            del self.entries[: len(self.entries) - MAX_ENTRIES]
        # byte cap: drop from the front until under the limit
        while len(self.entries) > 2:
            size = sum(len(e["text"]) for e in self.entries)
            if size <= MAX_BYTES:
                break
            del self.entries[0]

    def save(self):
        try:
            os.makedirs(history_dir(), exist_ok=True)
            root = {"key": self.key, "title": self.title, "updated": _now(), "entries": self.entries}
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(root, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def clear(self):
        self.entries = []
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except Exception:
            pass

    def tail(self, n=SHOW_ON_OPEN):
        return self.entries[-n:]

    def to_prompt(self):
        """Short excerpt of earlier sessions for the system prompt, or None."""
        ents = [e for e in self.entries if e["role"] in ("user", "assistant")][-PROMPT_ENTRIES:]
        if not ents:
            return None
        lines = []
        for e in ents:
            t = e["text"].replace("\n", " ")
            if len(t) > PROMPT_TEXT:
                t = t[:PROMPT_TEXT] + "…"
            lines.append("%s %s: %s" % (e["ts"][:16], "User" if e["role"] == "user" else "You", t))
        return ("# Recent conversation about this document (earlier sessions, for continuity - "
                "the document may have changed since)\n" + "\n".join(lines))


_cleaned = False


def cleanup():
    """Folder-level recycling; cheap, runs once per session."""
    global _cleaned
    if _cleaned:
        return
    _cleaned = True
    d = history_dir()
    try:
        if not os.path.isdir(d):
            return
        files = []
        now = time.time()
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if not n.endswith(".json") or not os.path.isfile(p):
                continue
            st = os.stat(p)
            if now - st.st_mtime > MAX_AGE_DAYS * 86400:
                os.remove(p)
                continue
            files.append((st.st_mtime, st.st_size, p))
        total = sum(f[1] for f in files)
        files.sort()                                  # oldest first
        while total > MAX_TOTAL_BYTES and files:
            _, size, p = files.pop(0)
            try:
                os.remove(p)
                total -= size
            except Exception:
                pass
    except Exception:
        pass
