# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""Drives the Claude tool-use loop, with one conversation per document.

ask() runs on a worker thread (network I/O). Everything that touches FreeCAD goes through
`main_call(fn)`, which the UI implements as a blocking hop to the GUI thread.
"""
import copy
import time

from . import client as claude
from . import context, history, memory, telemetry, tools

PERSONA = (
    "You are FreeGAD, an AI assistant embedded inside FreeCAD. You help the user understand, "
    "audit and modify the active document: parametric modelling (Part, PartDesign, Sketcher), "
    "spreadsheets and expressions, assemblies, meshes, and preparing models for 3D printing or "
    "manufacturing. You can do engineering calculations (volumes, masses, clearances, fits).\n\n"
    "A compact snapshot of the document is given below. For anything not in it - exact property "
    "values, face/edge detail, sketch constraints, spreadsheet cells - call the tools, which read "
    "the LIVE document (always current). take_screenshot lets you see the 3D view: use it when "
    "shape, orientation or appearance matters, and to verify your own edits. Prefer real data from "
    "tools over assumptions. When you compute, show the formula and inputs.\n\n"
    "When the user asks you to CHANGE the model, do it yourself with the write tools - run_python "
    "is the general mechanism (full FreeCAD Python API, runs in one undo step); set_property / "
    "set_expression / delete_object are shortcuts. Never tell the user to finish an edit manually "
    "and never open interactive dialogs from code. Each write tool asks the user to confirm, so "
    "just call it. After edits, verify (get_object / recompute / screenshot) and briefly confirm "
    "what changed. Be economical with tool calls: every call re-sends the conversation, so batch "
    "related work into one run_python, ask get_object only for objects you need, and take at most "
    "one screenshot per edit cycle. Object Names are internal ids (e.g. 'Pad001'); Labels are what the user sees - "
    "refer to objects by Label in prose and by Name in tools. Answer concisely; lead with the result.\n\n"
    "You keep notes between sessions with the remember tool: scope='document' for facts about the "
    "current file, scope='user' for how this person works. Be PROACTIVE about this - it is what "
    "makes you useful next time:\n"
    "- The first time you work on a document that has no notes yet, save 1-3 document notes: what "
    "the model is, its main parts/parameters, and its purpose if known.\n"
    "- After any substantive analysis or edit, save the durable conclusions: problems found and "
    "whether they were fixed, design intent, decisions made, values agreed with the user "
    "(e.g. 'Wall thickness chosen 2.4 mm for PA66'). Not raw numbers you can re-measure - the "
    "interpretation and the decision.\n"
    "- Save a user note whenever they state a preference, correct you, or reveal a habit "
    "(units, material, printer, tolerance, how they like answers, what project they are on).\n"
    "- If the user says 'remember ...', always save it exactly as asked.\n"
    "- If a note turns out wrong or stale, forget it and save the corrected one.\n"
    "Write each note as one self-contained sentence that will make sense months from now. "
    "Saving is silent: don't announce it or ask permission, and never let it interrupt the answer "
    "the user asked for. Notes you already have appear under '# Memory' below."
)

MAX_ITERATIONS = 40
MAX_TOOL_RESULT_CHARS = 14000      # one tool result; bigger ones are truncated with a note
OLD_RESULT_KEEP_CHARS = 600        # tool results from earlier turns are shrunk to this

# USD per 1M tokens: input, output, cache read, cache write (list prices; estimate only).
# Matched by prefix after stripping an OpenRouter-style "vendor/" prefix; longer ids first.
PRICES = [
    ("claude-opus-5", (5, 25, 0.5, 6.25)), ("claude-fable-5", (10, 50, 1, 12.5)),
    ("claude-opus-4-8", (5, 25, 0.5, 6.25)), ("claude-opus-4-7", (5, 25, 0.5, 6.25)),
    ("claude-opus-4-6", (5, 25, 0.5, 6.25)), ("claude-sonnet-5", (3, 15, 0.3, 3.75)),
    ("claude-sonnet-4-6", (3, 15, 0.3, 3.75)), ("claude-haiku-4-5", (1, 5, 0.1, 1.25)),
    ("gpt-5-mini", (0.25, 2, 0.025, 0)), ("gpt-5-nano", (0.05, 0.4, 0.005, 0)), ("gpt-5", (1.25, 10, 0.125, 0)),
    ("gpt-4.1-mini", (0.4, 1.6, 0.1, 0)), ("gpt-4.1", (2, 8, 0.5, 0)),
    ("o4-mini", (1.1, 4.4, 0.275, 0)), ("o3", (2, 8, 0.5, 0)),
]


def estimate_cost(model, inp, out, cache_read, cache_write):
    """List-price estimate, or None when the model's price is unknown (OpenRouter reports the real cost instead)."""
    mid = (model or "").strip().lower().split("/")[-1]
    for old, new in (("claude-opus-4.", "claude-opus-4-"), ("claude-sonnet-4.", "claude-sonnet-4-"),
                     ("claude-haiku-4.", "claude-haiku-4-")):
        mid = mid.replace(old, new)
    for prefix, p in PRICES:
        if mid.startswith(prefix):
            return (inp * p[0] + out * p[1] + cache_read * p[2] + cache_write * p[3]) / 1e6
    return None


def compact_history(history):
    """Before a new turn: shrink tool results of earlier turns (they are already reflected in the
    assistant's answers) and drop old screenshots. Claude can always re-call a tool for detail."""
    for msg in history:
        if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
            continue
        for block in msg["content"]:
            if block.get("type") != "tool_result":
                continue
            c = block.get("content")
            if isinstance(c, list):
                texts = [b.get("text", "") for b in c if b.get("type") == "text"]
                had_image = any(b.get("type") == "image" for b in c)
                c = ("[screenshot omitted from history] " if had_image else "") + " ".join(texts)
            if isinstance(c, str) and len(c) > OLD_RESULT_KEEP_CHARS:
                c = c[:OLD_RESULT_KEEP_CHARS] + "\n…[older result shortened; call the tool again if you need it]"
            block["content"] = c


class DocState:
    def __init__(self):
        self.system = None
        self.history = []
        self.user_mem = None
        self.doc_mem = None
        self.doc = None


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = claude.make_client(cfg)
        self.tools = tools.schemas()
        self._states = {}
        self.session_cost = 0.0

    def set_config(self, cfg):
        self.cfg = cfg
        self.client = claude.make_client(cfg)

    def _key(self, doc):
        return doc.Name if doc is not None else "__nodoc__"

    def state(self, doc, main_call):
        key = self._key(doc)
        s = self._states.get(key)
        if s is not None:
            return s
        s = DocState()
        s.doc = doc
        ctx = main_call(lambda: context.context_text(doc))
        s.user_mem = memory.Memory.load_user()
        s.doc_mem = memory.Memory.load_document(doc) if doc is not None else None

        # Order matters for prompt caching: persona and snapshot are stable -> cache breakpoint on
        # the snapshot. Memory goes AFTER it because it changes whenever a note is saved.
        s.system = [
            {"type": "text", "text": PERSONA},
            {"type": "text", "text": "# Current document context (compact)\n" + ctx,
             "cache_control": {"type": "ephemeral"}},
        ]
        block = memory.build_memory_block(s.user_mem, s.doc_mem)
        if block:
            s.system.append({"type": "text", "text": block})
        recent = history.History(doc).to_prompt() if doc is not None else None
        if recent:
            s.system.append({"type": "text", "text": recent})
        self._states[key] = s
        return s

    def reset(self, doc):
        self._states.pop(self._key(doc), None)

    def memories(self, doc):
        s = self._states.get(self._key(doc))
        if s:
            return s.user_mem, s.doc_mem
        return memory.Memory.load_user(), (memory.Memory.load_document(doc) if doc else None)

    def ask(self, doc, user_text, output, status, main_call, confirm):
        """One user turn. output(text) streams assistant text; status(text) shows progress."""
        s = self.state(doc, main_call)
        compact_history(s.history)
        s.history.append({"role": "user", "content": user_text})
        env = {"doc": doc, "user_mem": s.user_mem, "doc_mem": s.doc_mem,
               "config": self.cfg, "confirm": confirm}
        try:
            n_objs = main_call(lambda: len(doc.Objects)) if doc is not None else 0
        except Exception:
            n_objs = None
        m = telemetry.TurnMetrics(self.cfg.model, self.cfg.effort, n_objs, len(user_text))
        m.provider = self.cfg.provider
        try:
            self._run(s, env, m, output, status, main_call)
        except Exception as ex:
            m.error = type(ex).__name__ + ": " + str(ex)[:200]
            raise
        finally:
            telemetry.send("turn", m.payload())
            cost = m.cost_usd if m.has_cost else \
                estimate_cost(self.cfg.model, m.input_tokens, m.output_tokens, m.cache_read, m.cache_create)
            if cost is not None:
                self.session_cost += cost
                cost_txt = "≈ ${:.2f} this turn, ${:.2f} this session".format(cost, self.session_cost)
            else:
                cost_txt = "cost n/a (no list price known for %s)" % self.cfg.model
            output("\n\n_{} calls · in {:,} (+{:,} cached) · out {:,} · {}_\n".format(
                m.api_calls, m.input_tokens, m.cache_read, m.output_tokens, cost_txt))

    def _run(self, s, env, m, output, status, main_call):
        for _ in range(MAX_ITERATIONS):
            status("Thinking…")
            t_api = time.time()
            try:
                resp = self.client.create_message(s.system, s.history, self.tools)
            except Exception:
                s.history.pop()  # drop the user turn so the conversation stays valid for a retry
                raise
            m.api((time.time() - t_api) * 1000, resp)

            content = resp.get("content") or []
            s.history.append({"role": "assistant", "content": copy.deepcopy(content)})
            stop = resp.get("stop_reason")
            m.stop_reason = stop

            tool_results = []
            for block in content:
                t = block.get("type")
                if t == "text":
                    if block.get("text"):
                        m.response_chars += len(block["text"])
                        output(block["text"])
                elif t == "fallback":
                    m.fallback = True
                    output(f"\n_(served by fallback model {block.get('to', {}).get('model')})_\n")
                elif t == "tool_use":
                    tname = block.get("name")
                    tid = block.get("id")
                    tinput = block.get("input") or {}
                    status(f"Calling {tname}…")
                    output(f"\n`[{tname}]`\n")
                    t0, c0 = time.time(), time.process_time()
                    ok = True
                    try:
                        result = main_call(lambda: tools.execute(tname, tinput, env))
                        if isinstance(result, str) and len(result) > MAX_TOOL_RESULT_CHARS:
                            result = result[:MAX_TOOL_RESULT_CHARS] + "\n…[truncated: result too large; ask for a narrower query]"
                        tr = {"type": "tool_result", "tool_use_id": tid, "content": result}
                    except Exception as ex:
                        ok = False
                        tr = {"type": "tool_result", "tool_use_id": tid,
                              "content": f"Error: {ex}", "is_error": True}
                    declined = isinstance(tr.get("content"), str) and tr["content"].startswith("User declined")
                    m.tool(tname, (time.time() - t0) * 1000, (time.process_time() - c0) * 1000, ok,
                           confirmed=(not declined) if tname in tools.WRITE_TOOLS else None)
                    tool_results.append(tr)

            if stop == "refusal":
                det = resp.get("stop_details") or {}
                output("\n_(The model declined this request"
                       + (f": {det.get('explanation')}" if det.get("explanation") else "") + ")_\n")
                return
            if stop == "max_tokens":
                output("\n_(output truncated: max_tokens reached)_\n")
            if not tool_results:
                return
            s.history.append({"role": "user", "content": tool_results})
        output("\n_(stopped: too many tool iterations)_\n")
