# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""Persistent notes the agent keeps between sessions, in %APPDATA%\\FreeGAD\\memory\\:

    user.json                facts about this user - conventions, preferences, project meta
    documents\\<key>.json     facts about one FreeCAD document

Documents are keyed by App.Document.Uid, which FreeCAD stamps when the document is created
and stores inside the .FCStd, so the notes follow the file across saves, renames and moves.
Nothing is ever written into the document itself.
"""
import datetime
import hashlib
import json
import os
import uuid

from . import config

MAX_ENTRIES = 200
MAX_TEXT = 2000


def memory_dir():
    return os.path.join(config.app_dir(), "memory")


def documents_dir():
    return os.path.join(memory_dir(), "documents")


def user_path():
    return os.path.join(memory_dir(), "user.json")


def document_key(doc):
    raw = None
    try:
        uid = (doc.Uid or "").strip()
        if uid:
            raw = "uid:" + uid.lower()
    except Exception:
        pass
    if raw is None:
        try:
            fn = doc.FileName or ""
        except Exception:
            fn = ""
        raw = "path:" + (fn or "unsaved").lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _new_id():
    return uuid.uuid4().hex[:6]


class Entry:
    __slots__ = ("id", "text", "category", "created")

    def __init__(self, id, text, category=None, created=None):
        self.id = id
        self.text = text
        self.category = category
        self.created = created or datetime.datetime.now().isoformat(timespec="seconds")

    def to_json(self):
        return {"id": self.id, "text": self.text, "category": self.category, "created": self.created}

    @staticmethod
    def from_json(o):
        if not isinstance(o, dict):
            return None
        text = (o.get("text") or "").strip()
        if not text:
            return None
        return Entry(o.get("id") or _new_id(), text, o.get("category") or None, o.get("created"))


class Memory:
    def __init__(self, path, scope, title):
        self._path = path
        self.scope = scope          # "user" or "document"
        self.title = title
        self.entries = []
        self._load()

    @staticmethod
    def load_user():
        return Memory(user_path(), "user", os.environ.get("USERNAME") or os.environ.get("USER") or "user")

    @staticmethod
    def load_document(doc):
        key = document_key(doc)
        try:
            title = doc.Label or doc.Name
            if doc.FileName:
                title = os.path.basename(doc.FileName)
        except Exception:
            title = "(document)"
        return Memory(os.path.join(documents_dir(), key + ".json"), "document", title)

    def _load(self):
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path, "r", encoding="utf-8") as f:
                root = json.load(f)
            for node in root.get("entries", []):
                e = Entry.from_json(node)
                if e:
                    self.entries.append(e)
        except Exception:
            pass  # a corrupt memory file must never block the agent

    def save(self):
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            if len(self.entries) > MAX_ENTRIES:
                del self.entries[: len(self.entries) - MAX_ENTRIES]
            root = {
                "scope": self.scope,
                "title": self.title,
                "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                "entries": [e.to_json() for e in self.entries],
            }
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(root, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception:
            pass

    def add(self, text, category=None):
        text = (text or "").strip()
        if not text:
            raise ValueError("Memory text is empty.")
        text = text[:MAX_TEXT]
        for e in self.entries:
            if e.text.lower() == text.lower():
                e.category = category or e.category
                self.save()
                return e
        e = Entry(_new_id(), text, (category or "").strip() or None)
        self.entries.append(e)
        self.save()
        return e

    def remove(self, id):
        for e in self.entries:
            if e.id.lower() == (id or "").lower():
                self.entries.remove(e)
                self.save()
                return e.text
        return None

    def clear(self):
        self.entries.clear()
        self.save()

    def to_prompt(self):
        if not self.entries:
            return None
        lines = []
        for e in self.entries:
            cat = f"({e.category}) " if e.category else ""
            lines.append(f"- [{e.id}] {cat}{e.text}")
        return "\n".join(lines) + "\n"

    def to_display(self):
        if not self.entries:
            return "  (nothing remembered yet)"
        lines = []
        for e in self.entries:
            cat = f"({e.category}) " if e.category else ""
            lines.append(f"  [{e.id}] {cat}{e.text}   -- {e.created[:10]}")
        return "\n".join(lines)


def build_memory_block(user_mem, doc_mem):
    """Render both stores for the system prompt, or None when both are empty."""
    user = user_mem.to_prompt() if user_mem else None
    docm = doc_mem.to_prompt() if doc_mem else None
    if not user and not docm:
        return None
    sb = [
        "# Memory (notes you saved earlier)",
        "These are your own durable notes, not instructions from the user. Treat them as",
        "context that may be stale: if the document now contradicts a note, believe the document",
        "and call forget on the note. The [id] in brackets is what forget takes.",
        "",
    ]
    if docm:
        sb.append(f"## About this document ({doc_mem.title})")
        sb.append(docm)
    if user:
        sb.append("## About this user (applies to every document)")
        sb.append(user)
    return "\n".join(sb)
