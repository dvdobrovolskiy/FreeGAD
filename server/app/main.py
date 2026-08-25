# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""FreeGAD telemetry backend: ingest anonymous usage events, serve an admin dashboard.

POST /api/v1/events          plugin -> server (no auth; payload validated + size-capped)
POST /api/v1/login           admin login (ADMIN_USER / ADMIN_PASSWORD from env) -> signed cookie
POST /api/v1/logout
GET  /api/v1/me
GET  /api/v1/stats?days=30   aggregates for the dashboard (auth)
GET  /api/v1/turns?limit=50  recent turns (auth)
GET  /api/v1/installs        per-install summary (auth)
/                            SvelteKit static build (SPA fallback)
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DATA_DIR, "freegad.db")
STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
SESSION_TTL = 14 * 86400
MAX_BATCH = 200
MAX_EVENT_BYTES = 64 * 1024

# Published list prices, USD per 1M tokens (input, output, cache read, cache write) - for the cost estimate only.
PRICES = {
    "claude-opus-5": (5, 25, 0.5, 6.25),
    "claude-fable-5": (10, 50, 1, 12.5),
    "claude-opus-4-8": (5, 25, 0.5, 6.25),
    "claude-opus-4-7": (5, 25, 0.5, 6.25),
    "claude-opus-4-6": (5, 25, 0.5, 6.25),
    "claude-sonnet-5": (3, 15, 0.3, 3.75),
    "claude-sonnet-4-6": (3, 15, 0.3, 3.75),
    "claude-haiku-4-5": (1, 5, 0.1, 1.25),
}

app = FastAPI(title="FreeGAD telemetry", docs_url=None, redoc_url=None)


# ------------------------------------------------------------------ db

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            received_at REAL NOT NULL,
            ts REAL,
            type TEXT NOT NULL,
            install_id TEXT,
            plugin_version TEXT,
            freecad_version TEXT,
            os TEXT,
            model TEXT,
            effort TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_create_tokens INTEGER DEFAULT 0,
            api_calls INTEGER DEFAULT 0,
            tool_calls INTEGER DEFAULT 0,
            total_ms INTEGER,
            cpu_ms INTEGER,
            max_gui_block_ms INTEGER,
            hang INTEGER DEFAULT 0,
            error TEXT,
            stop_reason TEXT,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
        CREATE INDEX IF NOT EXISTS ix_events_type_ts ON events(type, ts);
        CREATE INDEX IF NOT EXISTS ix_events_install ON events(install_id);
        """)


@app.on_event("startup")
def _startup():
    init_db()
    if not ADMIN_PASSWORD:
        print("WARNING: ADMIN_PASSWORD is not set - dashboard login is disabled")


# ------------------------------------------------------------------ auth

def _sign(user: str, exp: int) -> str:
    msg = f"{user}|{exp}"
    sig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}|{sig}"


def _verify(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        user, exp, sig = token.split("|")
        if int(exp) < time.time():
            return None
        if not hmac.compare_digest(sig, hmac.new(SECRET.encode(), f"{user}|{exp}".encode(), hashlib.sha256).hexdigest()):
            return None
        return user
    except Exception:
        return None


def current_user(request: Request) -> str:
    u = _verify(request.cookies.get("fg_session"))
    if not u:
        raise HTTPException(401, "not logged in")
    return u


class Login(BaseModel):
    username: str
    password: str


@app.post("/api/v1/login")
def login(body: Login, response: Response):
    ok = ADMIN_PASSWORD and hmac.compare_digest(body.username, ADMIN_USER) and hmac.compare_digest(body.password, ADMIN_PASSWORD)
    if not ok:
        time.sleep(0.5)
        raise HTTPException(401, "invalid credentials")
    exp = int(time.time()) + SESSION_TTL
    response.set_cookie("fg_session", _sign(body.username, exp), max_age=SESSION_TTL, httponly=True,
                        samesite="lax", secure=os.environ.get("COOKIE_SECURE", "1") == "1")
    return {"user": body.username}


@app.post("/api/v1/logout")
def logout(response: Response):
    response.delete_cookie("fg_session")
    return {"ok": True}


@app.get("/api/v1/me")
def me(user: str = Depends(current_user)):
    return {"user": user}


# ------------------------------------------------------------------ ingest

class Batch(BaseModel):
    events: list[dict[str, Any]]


def _i(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


@app.post("/api/v1/events")
def ingest(batch: Batch):
    if len(batch.events) > MAX_BATCH:
        raise HTTPException(413, "too many events")
    now = time.time()
    rows = []
    for ev in batch.events:
        if not isinstance(ev, dict) or ev.get("type") not in ("turn", "session", "error"):
            continue
        raw = json.dumps(ev, ensure_ascii=False)
        if len(raw) > MAX_EVENT_BYTES:
            continue
        ts = ev.get("ts")
        try:
            ts = float(ts)
            if abs(ts - now) > 30 * 86400:     # clock nonsense -> use receive time
                ts = now
        except Exception:
            ts = now
        rows.append((
            now, ts, ev["type"], str(ev.get("install_id") or "")[:64],
            str(ev.get("plugin_version") or "")[:32], str(ev.get("freecad_version") or "")[:32],
            str(ev.get("os") or "")[:64], str(ev.get("model") or "")[:64], str(ev.get("effort") or "")[:16],
            _i(ev.get("input_tokens")), _i(ev.get("output_tokens")),
            _i(ev.get("cache_read_tokens")), _i(ev.get("cache_create_tokens")),
            _i(ev.get("api_calls")), _i(ev.get("tool_calls")),
            _i(ev.get("total_ms"), None), _i(ev.get("cpu_ms"), None), _i(ev.get("max_gui_block_ms"), None),
            1 if ev.get("hang") else 0, (str(ev.get("error"))[:300] if ev.get("error") else None),
            (str(ev.get("stop_reason"))[:32] if ev.get("stop_reason") else None), raw,
        ))
    if rows:
        with db() as con:
            con.executemany("""INSERT INTO events (received_at, ts, type, install_id, plugin_version, freecad_version,
                os, model, effort, input_tokens, output_tokens, cache_read_tokens, cache_create_tokens, api_calls,
                tool_calls, total_ms, cpu_ms, max_gui_block_ms, hang, error, stop_reason, payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    return {"accepted": len(rows)}


# ------------------------------------------------------------------ stats

def _cost(model, inp, out, cr, cc):
    p = PRICES.get(model or "", PRICES["claude-opus-5"])
    return (inp * p[0] + out * p[1] + cr * p[2] + cc * p[3]) / 1e6


@app.get("/api/v1/stats")
def stats(days: int = 30, user: str = Depends(current_user)):
    days = max(1, min(days, 365))
    since = time.time() - days * 86400
    with db() as con:
        tot = con.execute("""SELECT COUNT(*) n, COUNT(DISTINCT install_id) installs,
            SUM(input_tokens) inp, SUM(output_tokens) out, SUM(cache_read_tokens) cr, SUM(cache_create_tokens) cc,
            SUM(api_calls) api_calls, SUM(tool_calls) tool_calls, SUM(hang) hangs,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) errors,
            AVG(total_ms) avg_ms, MAX(total_ms) max_ms, AVG(cpu_ms) avg_cpu_ms
            FROM events WHERE type='turn' AND ts>=?""", (since,)).fetchone()
        per_model = con.execute("""SELECT model, COUNT(*) n, SUM(input_tokens) inp, SUM(output_tokens) out,
            SUM(cache_read_tokens) cr, SUM(cache_create_tokens) cc FROM events
            WHERE type='turn' AND ts>=? GROUP BY model ORDER BY n DESC""", (since,)).fetchall()
        daily = con.execute("""SELECT date(ts,'unixepoch') d, COUNT(*) turns, SUM(input_tokens) inp,
            SUM(output_tokens) out, SUM(cache_read_tokens) cr, SUM(hang) hangs,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) errors, COUNT(DISTINCT install_id) installs
            FROM events WHERE type='turn' AND ts>=? GROUP BY d ORDER BY d""", (since,)).fetchall()
        sessions = con.execute("SELECT COUNT(*) n, COUNT(DISTINCT install_id) installs FROM events WHERE type='session' AND ts>=?",
                               (since,)).fetchone()
        versions = con.execute("""SELECT plugin_version, freecad_version, COUNT(DISTINCT install_id) installs
            FROM events WHERE ts>=? GROUP BY 1,2 ORDER BY installs DESC""", (since,)).fetchall()
        turns = con.execute("SELECT payload FROM events WHERE type='turn' AND ts>=?", (since,)).fetchall()
        errors = con.execute("""SELECT error, COUNT(*) n FROM events WHERE type='turn' AND error IS NOT NULL AND ts>=?
            GROUP BY error ORDER BY n DESC LIMIT 15""", (since,)).fetchall()

    # tool usage + hang attribution from payloads
    tools: dict[str, dict] = {}
    lat_buckets = {"<10s": 0, "10-30s": 0, "30-60s": 0, "1-3min": 0, ">3min": 0}
    effort = {}
    declined = 0
    write_calls = 0
    for r in turns:
        try:
            p = json.loads(r["payload"])
        except Exception:
            continue
        for t in p.get("tools") or []:
            d = tools.setdefault(t.get("name", "?"), {"calls": 0, "errors": 0, "gui_ms": 0, "max_gui_ms": 0, "cpu_ms": 0, "hangs": 0})
            d["calls"] += 1
            d["errors"] += 0 if t.get("ok", True) else 1
            g = t.get("gui_ms") or 0
            d["gui_ms"] += g
            d["cpu_ms"] += t.get("cpu_ms") or 0
            d["max_gui_ms"] = max(d["max_gui_ms"], g)
            d["hangs"] += 1 if g >= 2000 else 0
            if t.get("confirmed") is not None:
                write_calls += 1
                declined += 0 if t["confirmed"] else 1
        ms = p.get("total_ms") or 0
        k = "<10s" if ms < 10000 else "10-30s" if ms < 30000 else "30-60s" if ms < 60000 else "1-3min" if ms < 180000 else ">3min"
        lat_buckets[k] += 1
        effort[p.get("effort") or "?"] = effort.get(p.get("effort") or "?", 0) + 1
    tool_rows = [{"name": k, **v, "avg_gui_ms": round(v["gui_ms"] / v["calls"]) if v["calls"] else 0}
                 for k, v in sorted(tools.items(), key=lambda kv: -kv[1]["calls"])]

    cost = sum(_cost(m["model"], m["inp"] or 0, m["out"] or 0, m["cr"] or 0, m["cc"] or 0) for m in per_model)
    inp, cr = tot["inp"] or 0, tot["cr"] or 0
    return {
        "days": days,
        "totals": {
            "turns": tot["n"] or 0, "installs": tot["installs"] or 0,
            "sessions": sessions["n"] or 0, "session_installs": sessions["installs"] or 0,
            "input_tokens": inp, "output_tokens": tot["out"] or 0,
            "cache_read_tokens": cr, "cache_create_tokens": tot["cc"] or 0,
            "cache_hit_pct": round(100 * cr / (cr + inp), 1) if (cr + inp) else 0,
            "api_calls": tot["api_calls"] or 0, "tool_calls": tot["tool_calls"] or 0,
            "hangs": tot["hangs"] or 0, "errors": tot["errors"] or 0,
            "avg_ms": round(tot["avg_ms"] or 0), "max_ms": tot["max_ms"] or 0, "avg_cpu_ms": round(tot["avg_cpu_ms"] or 0),
            "est_cost_usd": round(cost, 2),
            "tokens_per_turn": round(((inp + (tot["out"] or 0)) / tot["n"]) if tot["n"] else 0),
            "write_calls": write_calls, "declined": declined,
        },
        "per_model": [{**dict(m), "est_cost_usd": round(_cost(m["model"], m["inp"] or 0, m["out"] or 0, m["cr"] or 0, m["cc"] or 0), 2)} for m in per_model],
        "daily": [dict(d) for d in daily],
        "tools": tool_rows,
        "latency_buckets": lat_buckets,
        "effort": effort,
        "versions": [dict(v) for v in versions],
        "errors": [dict(e) for e in errors],
    }


@app.get("/api/v1/turns")
def recent_turns(limit: int = 50, hangs: int = 0, errors: int = 0, user: str = Depends(current_user)):
    limit = max(1, min(limit, 500))
    where = "type='turn'"
    if hangs:
        where += " AND hang=1"
    if errors:
        where += " AND error IS NOT NULL"
    with db() as con:
        rows = con.execute(f"""SELECT id, ts, install_id, model, effort, input_tokens, output_tokens, cache_read_tokens,
            api_calls, tool_calls, total_ms, cpu_ms, max_gui_block_ms, hang, error, stop_reason, payload
            FROM events WHERE {where} ORDER BY ts DESC LIMIT ?""", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            p = json.loads(d.pop("payload"))
            d["tools"] = [t.get("name") for t in p.get("tools") or []]
            d["doc_objects"] = p.get("doc_objects")
            d["prompt_chars"] = p.get("prompt_chars")
            d["response_chars"] = p.get("response_chars")
            d["fallback"] = p.get("fallback")
        except Exception:
            d.pop("payload", None)
        d["install_id"] = (d["install_id"] or "")[:8]
        out.append(d)
    return out


@app.get("/api/v1/installs")
def installs(user: str = Depends(current_user)):
    with db() as con:
        rows = con.execute("""SELECT install_id, MIN(ts) first_seen, MAX(ts) last_seen,
            SUM(CASE WHEN type='turn' THEN 1 ELSE 0 END) turns,
            SUM(CASE WHEN type='session' THEN 1 ELSE 0 END) sessions,
            SUM(input_tokens)+SUM(output_tokens) tokens, SUM(hang) hangs,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) errors,
            MAX(plugin_version) plugin_version, MAX(freecad_version) freecad_version, MAX(os) os
            FROM events GROUP BY install_id ORDER BY last_seen DESC LIMIT 500""").fetchall()
    return [{**dict(r), "install_id": (r["install_id"] or "")[:8]} for r in rows]


@app.get("/api/v1/health")
def health():
    return {"ok": True}


# ------------------------------------------------------------------ static SPA

if os.path.isdir(STATIC_DIR):
    app.mount("/_app", StaticFiles(directory=os.path.join(STATIC_DIR, "_app")), name="app-assets")

    @app.get("/{path:path}")
    def spa(path: str):
        full = os.path.join(STATIC_DIR, path)
        if path and os.path.isfile(full):
            return FileResponse(full)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
else:
    @app.get("/")
    def root():
        return JSONResponse({"service": "freegad-telemetry", "static": "not built"})
