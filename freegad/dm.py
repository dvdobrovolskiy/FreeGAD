# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""dm - metrics client for Python apps and backends. Stdlib only, one file, never raises, never blocks.

Desktop / plugin (FreeGAD inside FreeCAD, scripts, CLI tools):

    import dm
    dm.init("freegad", version="1.0.3", app_dir=r"%APPDATA%\\FreeGAD")   # creates <app_dir>/metrics.json
    dm.event("turn", {"model": "claude-opus-5", "tools": 3})
    ... on exit nothing is required; session_end is sent by atexit (2 s budget, then it is spooled).

    init() emits "first_run" once per install id and "session" once per process (both ~5 s after init, on the
    worker thread, so start-up cost is zero). Opt-out: set "enabled": false in metrics.json, or env DM_DISABLE=1.

Backend (FastAPI on the VPS - dobrovolskiy.com, bro, ryta, ua):

    dm.init("site", version=GIT_SHA, server=True)
    dm.event("contact_submit", {"lang": "ru"}, did=dm.hash_id(ip + ua), sid=None)

    server=True: no install id file, plat="other", events carry whatever did/sid you pass (hash them first).

Batching: events are queued and posted every 10 s or when 25 are waiting; failures are spooled to
<app_dir>/metrics-pending.jsonl (capped at 256 KB, oldest dropped) and retried on the next flush.
"""
import atexit
import json
import locale
import os
import platform
import queue
import sys
import threading
import time
import urllib.request
import uuid

DEFAULT_HOST = "https://dobrovolskiy.com"
FLUSH_EVERY = 10.0
BATCH = 25
MAX_PENDING_BYTES = 256 * 1024
_state = {"ok": False}
_q: "queue.Queue" = queue.Queue()
_lock = threading.Lock()
_worker = None


def hash_id(s: str) -> str:
    import hashlib
    return hashlib.sha256(("dm|" + s).encode("utf-8")).hexdigest()[:16]


def _plat():
    s = platform.system()
    return {"Windows": "win", "Linux": "linux", "Darwin": "mac"}.get(s, "other")


def _ctx(version):
    try:
        lang = (locale.getlocale()[0] or "") if hasattr(locale, "getlocale") else ""
    except Exception:
        lang = ""
    osv = platform.release()
    if platform.system() == "Windows":
        osv = platform.version()
    return {"plat": _plat(), "os": platform.system(), "osv": osv, "arch": platform.machine(),
            "av": version, "lang": lang or None, "tz": time.strftime("%Z") or None}


def _load_cfg(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), False
    except Exception:
        cfg = {"id": uuid.uuid4().hex[:16], "enabled": True, "first_run": int(time.time())}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass
        return cfg, True


def init(project, version=None, app_dir=None, host=DEFAULT_HOST, server=False, session_props=None):
    """Call once. Safe to call from the GUI thread: does no I/O beyond reading/writing a tiny JSON file."""
    try:
        if os.environ.get("DM_DISABLE"):
            return
        st = {"p": project, "host": host.rstrip("/") + "/e", "ctx": _ctx(version), "server": server,
              "sid": uuid.uuid4().hex[:12], "t0": time.time(), "did": None, "dir": app_dir, "ok": True}
        first = False
        if not server:
            app_dir = app_dir or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.config"), project)
            st["dir"] = os.path.expandvars(app_dir)
            cfg, first = _load_cfg(os.path.join(st["dir"], "metrics.json"))
            if not cfg.get("enabled", True):
                return
            st["did"] = cfg.get("id")
        else:
            st["ctx"]["plat"] = "other"
        _state.clear()
        _state.update(st)
        if not server:
            if first:
                event("first_run")
            event("session", session_props)
            atexit.register(_exit)
            _ensure_worker(delay=5.0)     # first send ~5 s after start-up, never during it
    except Exception:
        _state["ok"] = False


def enabled():
    return bool(_state.get("ok"))


def event(name, props=None, did=None, sid=None, dur=None):
    """Queue one event. Cheap, non-blocking, never raises."""
    try:
        if not _state.get("ok"):
            return
        e = {"n": name, "t": time.time()}
        if props:
            e["pr"] = props
        if dur is not None:
            e["dur"] = dur
        if did:
            e["did"] = did
        if sid:
            e["sid"] = sid
        _q.put(e)
        if _q.qsize() >= BATCH:
            _ensure_worker(delay=0)
        else:
            _ensure_worker(delay=FLUSH_EVERY)
    except Exception:
        pass


def flush(timeout=2.0):
    """Best-effort synchronous send of what is queued (used at exit). Returns quickly on failure."""
    try:
        batch = _drain()
        if not batch:
            return
        _post(batch, timeout)
    except Exception:
        pass


# ------------------------------------------------------------------ internals

def _exit():
    try:
        dur = int(time.time() - _state["t0"])
        _q.put({"n": "session_end", "t": time.time(), "dur": dur})
        flush(2.0)
    except Exception:
        pass


def _ensure_worker(delay):
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, args=(delay,), name="dm-metrics", daemon=True)
            _worker.start()


def _drain():
    batch = _take_spool()
    try:
        while len(batch) < 100:
            batch.append(_q.get_nowait())
    except queue.Empty:
        pass
    return batch


def _loop(delay):
    time.sleep(delay)
    backoff = FLUSH_EVERY
    idle = 0
    while True:
        batch = _drain()
        if batch:
            try:
                _post(batch, 10)
                backoff = FLUSH_EVERY
                idle = 0
            except Exception:
                _spool(batch)
                backoff = min(backoff * 2, 300)
        else:
            idle += 1
            if idle > 6:          # a minute without events: let the thread die, event() restarts it
                return
        time.sleep(backoff)


def _envelope(batch):
    env = {"p": _state["p"], "v": 1, "ctx": _state["ctx"], "e": batch}
    if _state.get("did"):
        env["did"] = _state["did"]
    if not _state.get("server"):
        env["sid"] = _state["sid"]
    return env


def _post(batch, timeout):
    data = json.dumps(_envelope(batch), separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(_state["host"], data=data, method="POST")
    req.add_header("Content-Type", "text/plain")
    req.add_header("User-Agent", "dm-python/1 %s/%s" % (_state["p"], _state["ctx"].get("av")))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def _pending():
    d = _state.get("dir")
    return os.path.join(d, "metrics-pending.jsonl") if d else None


def _spool(events):
    p = _pending()
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > MAX_PENDING_BYTES:
            os.remove(p)
        with open(p, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _take_spool():
    p = _pending()
    try:
        if not p or not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        os.remove(p)
        return lines[-80:]
    except Exception:
        return []


if __name__ == "__main__":       # smoke test:  python dm.py [host]
    init("dm-test", version="0.0", app_dir=os.path.join(os.environ.get("TEMP", "/tmp"), "dm-test"),
         host=sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST)
    event("smoke", {"ok": True})
    flush()
    print("sent" if enabled() else "disabled")
