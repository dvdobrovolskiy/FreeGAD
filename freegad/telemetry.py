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
import ctypes
import json
import os
import platform
import queue
import threading
import time
import urllib.request

from . import config
from . import dm

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


def dm_init():
    """Marketing-level funnel (first_run / session / feature counts) to dobrovolskiy.com via the dm SDK.
    Same on/off switch as the LLM telemetry; same rule: never prompts, code, file names or contents."""
    try:
        if not config.Config.load().telemetry:
            return
        from . import __version__
        dm.init("freegad", version=__version__, app_dir=config.app_dir())
    except Exception:
        pass


def _dm_forward(event_type, payload):
    """Mirror a turn as a flat, content-free dm event (model, provider, tool count, latency, hang, error class)."""
    try:
        if event_type != "turn" or not dm.enabled():
            return
        payload = payload or {}
        err = payload.get("error")
        dm.event("turn", {
            "model": payload.get("model"),
            "provider": payload.get("provider"),
            "tools": payload.get("tool_calls") or 0,
            "ms": payload.get("total_ms") or 0,
            "hang": bool(payload.get("hang")),
            "error_class": (str(err).split(":", 1)[0][:60] if err else None),
            "crashed": bool(payload.get("crashed")),
        })
    except Exception:
        pass


def _pending_path():
    return os.path.join(config.app_dir(), "telemetry", "pending.jsonl")


def _inflight_path():
    return os.path.join(config.app_dir(), "telemetry", "inflight.json")


def mark_inflight(payload):
    """Remember the turn in progress. If FreeCAD dies (OOM, crash, reboot) before the turn's
    event is sent, flush_inflight() reports it on the next start - otherwise the worst turns,
    the ones that take the machine down, are exactly the ones we never see."""
    try:
        p = _inflight_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def clear_inflight():
    try:
        os.remove(_inflight_path())
    except Exception:
        pass


def flush_inflight():
    """Called at session start: a leftover in-flight marker becomes a 'turn' event with an error."""
    try:
        p = _inflight_path()
        if not os.path.exists(p):
            return
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
        os.remove(p)
        tool = payload.get("crashed_in") or "?"
        payload["error"] = "ProcessDied: FreeCAD ended during %s (crash / OOM / reboot); turn never finished" % tool
        payload["crashed"] = True
        payload["stop_reason"] = None
        send("turn", payload)
    except Exception:
        pass


# ------------------------------------------------------------------ memory probe

_MB = 1024 * 1024


def mem_status():
    """{'proc_mb': committed private bytes of this process, 'ws_mb': working set,
    'avail_mb': free physical RAM system-wide, 'total_mb': physical RAM} or None.
    Windows via ctypes; Linux via /proc; never raises."""
    try:
        if os.name == "nt":
            class PMC(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                            ("ullAvailExtendedVirtual", ctypes.c_uint64)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            try:
                fn = k32.K32GetProcessMemoryInfo
            except AttributeError:
                fn = ctypes.windll.psapi.GetProcessMemoryInfo
            fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_uint32]
            fn.restype = ctypes.c_int
            if not fn(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return None
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MS)]
            if not k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return None
            return {"proc_mb": pmc.PagefileUsage // _MB, "ws_mb": pmc.WorkingSetSize // _MB,
                    "avail_mb": ms.ullAvailPhys // _MB, "total_mb": ms.ullTotalPhys // _MB}
        proc = ws = avail = total = None
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    ws = int(line.split()[1]) // 1024
                elif line.startswith("VmSize:"):
                    proc = int(line.split()[1]) // 1024
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) // 1024
                elif line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
        return {"proc_mb": proc, "ws_mb": ws, "avail_mb": avail, "total_mb": total}
    except Exception:
        return None


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
        _dm_forward(event_type, payload)
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
        self.calls = []              # per API call: ms, in, cr (cache read), cw (cache write), out
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
        inp, out = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
        cr, cw = int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0)
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_read += cr
        self.cache_create += cw
        self.calls.append({"ms": round(ms), "in": inp, "cr": cr, "cw": cw, "out": out})
        if u.get("cost_usd") is not None:
            try:
                self.cost_usd += float(u["cost_usd"])
                self.has_cost = True
            except Exception:
                pass
        if resp and resp.get("model"):
            self.served_by = resp["model"]

    def tool(self, name, gui_ms, cpu_ms, ok, confirmed=None, mem=None):
        t = {"name": name, "gui_ms": round(gui_ms), "cpu_ms": round(cpu_ms),
             "ok": bool(ok), "confirmed": confirmed}
        if mem:
            t.update(mem)            # mem_delta_mb, mem_peak_mb, mem_avail_min_mb, mem_abort
        self.tools.append(t)

    def payload(self):
        total_ms = (time.time() - self.t0) * 1000
        cpu_ms = (time.process_time() - self.cpu0) * 1000
        max_block = max([t["gui_ms"] for t in self.tools] + [0])
        ctx = [c["in"] + c["cr"] + c["cw"] for c in self.calls]
        mem_peak = max([t.get("mem_peak_mb") or 0 for t in self.tools] + [0])
        mem_delta = max([t.get("mem_delta_mb") or 0 for t in self.tools] + [0])
        return {
            "model": self.model, "effort": self.effort, "served_by": self.served_by,
            "provider": self.provider, "cost_usd": self.cost_usd if self.has_cost else None,
            "doc_objects": self.doc_objects, "prompt_chars": self.prompt_chars,
            "response_chars": self.response_chars,
            "api_calls": self.api_calls, "api_ms": self.api_ms, "calls": self.calls,
            "ctx_avg": round(sum(ctx) / len(ctx)) if ctx else 0, "ctx_max": max(ctx) if ctx else 0,
            "mem_peak_mb": mem_peak, "mem_delta_mb": mem_delta,
            "mem_aborts": sum(1 for t in self.tools if t.get("mem_abort")),
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read, "cache_create_tokens": self.cache_create,
            "tools": self.tools, "tool_calls": len(self.tools),
            "total_ms": round(total_ms), "cpu_ms": round(cpu_ms),
            "max_gui_block_ms": max_block, "hang": max_block >= 2000,
            "stop_reason": self.stop_reason, "fallback": self.fallback,
            "error": self.error,
        }
