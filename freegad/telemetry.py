# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""Anonymous usage telemetry (opt-out, on by default).

What is sent: per-turn metrics keyed by a random install id - model/effort, token usage, API and
tool latencies, how long the GUI thread was blocked (hangs), process CPU time, error classes,
plugin/FreeCAD/OS versions, document size (object count).
What is NEVER sent: prompt or answer text, code Claude ran, file names, document contents, the API key.

Events are posted from a background thread; failures are spooled to
%APPDATA%\\FreeGAD\\telemetry\\pending.jsonl and retried on the next send.
"""
import json
import os
import platform
import queue
import threading
import time
import urllib.request

from . import config

DEFAULT_URL = "https://freecad.dobrovolskiy.com/api/v1/events"
MAX_PENDING_BYTES = 512 * 1024
_q = queue.Queue()
_worker = None
_lock = threading.Lock()


def enabled():
    try:
        return bool(config.Config.load().telemetry)
    except Exception:
        return False


def _pending_path():
    return os.path.join(config.app_dir(), "telemetry", "pending.jsonl")


def base_fields():
    cfg = config.Config.load()
    try:
        import FreeCAD as App
        fc = ".".join(str(x) for x in App.Version()[:3])
    except Exception:
        fc = None
    from . import __version__
    return {
        "install_id": cfg.install_id,
        "plugin_version": __version__,
        "freecad_version": fc,
        "os": platform.system() + " " + platform.release(),
        "python": platform.python_version(),
    }


def send(event_type, payload):
    """Queue one event. Cheap and never raises."""
    try:
        cfg = config.Config.load()
        if not cfg.telemetry:
            return
        ev = dict(base_fields())
        ev.update(payload or {})
        ev["type"] = event_type
        ev["ts"] = time.time()
        _q.put((cfg.telemetry_url or DEFAULT_URL, ev))
        _ensure_worker()
    except Exception:
        pass


def _ensure_worker():
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="freegad-telemetry", daemon=True)
            _worker.start()


def _post(url, events):
    data = json.dumps({"events": events}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "FreeGAD-telemetry")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def _spool(events):
    try:
        p = _pending_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > MAX_PENDING_BYTES:
            os.remove(p)                       # never let the spool grow without bound
        with open(p, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
    except Exception:
        pass


def _take_spool():
    p = _pending_path()
    try:
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        os.remove(p)
        return lines[-200:]
    except Exception:
        return []


def _loop():
    while True:
        try:
            url, ev = _q.get(timeout=30)
        except queue.Empty:
            return
        batch = [ev]
        # drain what else is waiting
        try:
            while True:
                _, e = _q.get_nowait()
                batch.append(e)
        except queue.Empty:
            pass
        batch = _take_spool() + batch
        try:
            _post(url, batch)
        except Exception:
            _spool(batch)


class TurnMetrics:
    """Accumulated by the agent during one user turn."""

    def __init__(self, model, effort, doc_objects, prompt_chars):
        self.t0 = time.time()
        self.cpu0 = time.process_time()
        self.model = model
        self.effort = effort
        self.doc_objects = doc_objects
        self.prompt_chars = prompt_chars
        self.api_calls = 0
        self.api_ms = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0
        self.cache_create = 0
        self.tools = []
        self.response_chars = 0
        self.stop_reason = None
        self.error = None
        self.fallback = False
        self.served_by = None
        self.provider = None
        self.cost_usd = 0.0          # summed from usage.cost_usd (OpenRouter)
        self.has_cost = False

    def api(self, ms, resp):
        self.api_calls += 1
        self.api_ms.append(round(ms))
        u = (resp or {}).get("usage") or {}
        self.input_tokens += int(u.get("input_tokens") or 0)
        self.output_tokens += int(u.get("output_tokens") or 0)
        self.cache_read += int(u.get("cache_read_input_tokens") or 0)
        self.cache_create += int(u.get("cache_creation_input_tokens") or 0)
        if u.get("cost_usd") is not None:
            try:
                self.cost_usd += float(u["cost_usd"])
                self.has_cost = True
            except Exception:
                pass
        if resp and resp.get("model"):
            self.served_by = resp["model"]

    def tool(self, name, gui_ms, cpu_ms, ok, confirmed=None):
        self.tools.append({"name": name, "gui_ms": round(gui_ms), "cpu_ms": round(cpu_ms),
                           "ok": bool(ok), "confirmed": confirmed})

    def payload(self):
        total_ms = (time.time() - self.t0) * 1000
        cpu_ms = (time.process_time() - self.cpu0) * 1000
        max_block = max([t["gui_ms"] for t in self.tools] + [0])
        return {
            "model": self.model, "effort": self.effort, "served_by": self.served_by,
            "provider": self.provider, "cost_usd": self.cost_usd if self.has_cost else None,
            "doc_objects": self.doc_objects, "prompt_chars": self.prompt_chars,
            "response_chars": self.response_chars,
            "api_calls": self.api_calls, "api_ms": self.api_ms,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read, "cache_create_tokens": self.cache_create,
            "tools": self.tools, "tool_calls": len(self.tools),
            "total_ms": round(total_ms), "cpu_ms": round(cpu_ms),
            "max_gui_block_ms": max_block, "hang": max_block >= 2000,
            "stop_reason": self.stop_reason, "fallback": self.fallback,
            "error": self.error,
        }
