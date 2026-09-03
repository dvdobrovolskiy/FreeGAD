# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""Tool schemas + executors. Every executor runs on the GUI thread (see ui.MainBridge)."""
import base64
import contextlib
import gc
import io
import json
import os
import sys
import tempfile
import time
import traceback

import FreeCAD as App

try:
    import FreeCADGui as Gui
except Exception:
    Gui = None

try:
    from PySide import QtCore, QtWidgets
except Exception:
    QtWidgets = None

from . import context, telemetry


# ------------------------------------------------------------------ schemas

def _tool(name, desc, props=None, required=None):
    schema = {"type": "object", "properties": {}}
    for pname, ptype, pdesc in (props or []):
        schema["properties"][pname] = {"type": ptype, "description": pdesc}
    if required:
        schema["required"] = list(required)
    return {"name": name, "description": desc, "input_schema": schema}


def schemas():
    return [
        _tool("get_context",
              "Refresh and return the compact snapshot of the active document: metadata, object list "
              "(name, label, type, dependencies, bbox/volume, placement), selection, workbench.",
              [("document", "string", "Optional document Name; defaults to the active document.")]),

        _tool("list_documents", "All open documents with object counts and which one is active."),

        _tool("list_objects",
              "List objects in the active document, optionally filtered by TypeId substring "
              "(e.g. 'Part::', 'PartDesign::Pad', 'Sketcher', 'Spreadsheet') and/or label substring.",
              [("type", "string", "TypeId substring filter (case-insensitive)."),
               ("label", "string", "Label/Name substring filter (case-insensitive)."),
               ("limit", "integer", "Max objects to return (default 100).")]),

        _tool("get_object",
              "Full detail of one object by Name (or Label): every property with type and value, "
              "expressions, detailed shape info (faces/edges/volume/center of mass), sketch geometry "
              "and constraints, spreadsheet cells.",
              [("name", "string", "Object Name (internal) or Label.")], ["name"]),

        _tool("get_selection", "Current GUI selection: objects and sub-elements (Face3, Edge1, ...)."),

        _tool("take_screenshot",
              "Render the active 3D view to an image and return it so you can SEE the model. "
              "Optional view direction and fit-all. Use this when geometry or appearance matters.",
              [("view", "string", "Optional standard view: front, top, right, rear, bottom, left, isometric."),
               ("fit", "boolean", "Fit all objects before capturing (default true)."),
               ("width", "integer", "Image width px (default 800)."),
               ("height", "integer", "Image height px (default 600).")]),

        _tool("measure",
              "Measure distance between two objects/sub-elements, or properties of one sub-element "
              "(e.g. 'Box.Face6' area/center, 'Cylinder.Edge1' length/radius).",
              [("a", "string", "'ObjectName' or 'ObjectName.SubElement' (e.g. 'Box.Face1')."),
               ("b", "string", "Optional second reference for a distance measurement.")], ["a"]),

        # ---- write tools (each prompts the user for confirmation unless auto-approve is on) ----
        _tool("run_python",
              "Execute Python in FreeCAD's interpreter with App/FreeCAD, Gui/FreeCADGui, Part, doc "
              "(active document) pre-bound. THIS is how you create, modify and delete geometry, "
              "sketches, PartDesign features, spreadsheets, expressions, placements - anything. "
              "Print what you want to see; stdout, the return value of a final expression, and a "
              "summary of added/removed objects are returned. The code runs inside one undo "
              "transaction and recomputes the document afterwards. Keep scripts focused; call "
              "get_object/get_context afterwards to verify the result. The user confirms before it runs. "
              "SPEED: the script blocks the whole GUI and is ABORTED after a time limit (120 s unless "
              "the user changed it in Settings), or as soon as it makes FreeCAD's memory grow by more "
              "than half of the machine's RAM (heavy booleans / tessellation on complex solids allocate "
              "gigabytes and freeze the whole PC; the user can disable this guard). Boolean operations "
              "(common/cut/slice/section) on complex solids cost seconds EACH - never loop them over "
              "many sample points/heights. Prefer BoundBox, distToShape, face/edge queries, or one "
              "slice call with several planes; split genuinely long jobs into multiple calls.",
              [("code", "string", "Python source to execute."),
               ("purpose", "string", "One line shown to the user describing what the code does.")],
              ["code", "purpose"]),

        _tool("set_property",
              "Set one property of one object (parsed like a Python literal, e.g. 10, '5 mm', "
              "[1,2,3], True, 'text'). Shortcut for simple edits; use run_python for anything else. "
              "The user confirms first.",
              [("name", "string", "Object Name or Label."),
               ("property", "string", "Property name, e.g. Length, Height, Label, Placement.Base.x"),
               ("value", "string", "New value, as a Python literal.")],
              ["name", "property", "value"]),

        _tool("set_expression",
              "Bind (or clear) a property expression, e.g. Height = 'Spreadsheet.height * 2'. "
              "The user confirms first.",
              [("name", "string", "Object Name or Label."),
               ("property", "string", "Property path, e.g. Height or Placement.Base.x"),
               ("expression", "string", "Expression text; empty string clears it.")],
              ["name", "property", "expression"]),

        _tool("delete_object",
              "Delete objects by Name. Dependents are reported, not deleted. The user confirms first.",
              [("names", "array", "List of object Names.")], ["names"]),

        _tool("set_visibility",
              "Show/hide objects in the 3D view. No confirmation needed.",
              [("names", "array", "List of object Names."),
               ("visible", "boolean", "true to show, false to hide.")], ["names", "visible"]),

        _tool("select",
              "Set the GUI selection to the given objects / sub-elements (e.g. 'Box', 'Box.Face2'). "
              "Empty list clears the selection.",
              [("refs", "array", "List of 'Object' or 'Object.SubElement' strings.")], ["refs"]),

        _tool("recompute", "Recompute the active document and report any objects in error state."),

        # ---- memory (no confirmation: these write notes, never the document) ----
        _tool("remember",
              "Save a durable note that will be given back to you in later sessions. Two scopes:\n"
              "  scope='document' - facts about THIS document: what it is and what it is for, design "
              "intent, which bodies/sketches/parameters drive what, problems found and fixed, decisions "
              "made with the user.\n"
              "  scope='user' - facts about THIS user that hold across documents: units, tolerances, "
              "printer/material, modelling habits (PartDesign vs Part), naming conventions, how they "
              "like answers presented, ongoing projects.\n"
              "Be proactive: save notes after substantive analysis/edits, on first contact with a new "
              "document, whenever the user states a preference or corrects you, and always when they "
              "say 'remember'. Don't save raw measurements you can re-read from the document - save the "
              "interpretation and the decision. One self-contained sentence per note.",
              [("scope", "string", "'document' (this file only) or 'user' (all documents)."),
               ("text", "string", "The fact, as one self-contained sentence."),
               ("category", "string", "Optional short tag, e.g. 'intent', 'decision', 'issue', 'convention', 'correction'.")],
              ["scope", "text"]),

        _tool("forget",
              "Delete a remembered note by its id (the [id] shown in the memory listing). Use when a "
              "note is wrong or out of date - if it is merely incomplete, save a corrected one instead.",
              [("scope", "string", "'document' or 'user' - which store the id belongs to."),
               ("id", "string", "The note id to delete.")],
              ["scope", "id"]),
    ]


WRITE_TOOLS = {"run_python", "set_property", "set_expression", "delete_object"}


# ------------------------------------------------------------------ helpers

def _j(o):
    return json.dumps(o, ensure_ascii=False, indent=1, default=str)


def _doc(env, name=None):
    if name:
        d = App.getDocument(name)
        if d is None:
            raise ValueError(f"No open document named '{name}'.")
        return d
    d = env.get("doc")
    if d is None:
        d = App.ActiveDocument
    if d is None:
        raise ValueError("No document is open. Create or open one first.")
    return d


def _find(doc, name):
    o = doc.getObject(name)
    if o is not None:
        return o
    hits = doc.getObjectsByLabel(name)
    if hits:
        return hits[0]
    low = name.lower()
    for o in doc.Objects:
        if o.Name.lower() == low or o.Label.lower() == low:
            return o
    raise ValueError(f"No object named or labelled '{name}'.")


def _split_ref(doc, ref):
    if "." in ref:
        oname, sub = ref.split(".", 1)
    else:
        oname, sub = ref, ""
    return _find(doc, oname), sub


def _subshape(obj, sub):
    shape = obj.Shape
    if not sub:
        return shape
    return shape.getElement(sub)


def _confirm(env, title, text):
    if env.get("config") is not None and env["config"].auto_approve:
        return True
    fn = env.get("confirm")
    if fn is None:
        return True
    return bool(fn(title, text))


# ------------------------------------------------------------------ executors

def execute(name, inp, env):
    """Return a string or a list of content blocks. Raises on error (caller marks is_error)."""
    fn = _EXEC.get(name)
    if fn is None:
        raise ValueError(f"Unknown tool '{name}'.")
    return fn(inp or {}, env)


def t_get_context(inp, env):
    return context.context_text(_doc(env, inp.get("document")))


def t_list_documents(inp, env):
    return _j(context.open_documents())


def t_list_objects(inp, env):
    doc = _doc(env)
    tf = (inp.get("type") or "").lower()
    lf = (inp.get("label") or "").lower()
    limit = int(inp.get("limit") or 100)
    out = []
    for o in doc.Objects:
        if tf and tf not in o.TypeId.lower():
            continue
        if lf and lf not in o.Label.lower() and lf not in o.Name.lower():
            continue
        out.append(context.object_brief(o))
        if len(out) >= limit:
            break
    return _j({"count": len(out), "objects": out})


def t_get_object(inp, env):
    doc = _doc(env)
    return _j(context.object_full(_find(doc, inp["name"])))


def t_get_selection(inp, env):
    return _j(context.selection_summary())


_VIEWS = {"front": "viewFront", "top": "viewTop", "right": "viewRight", "rear": "viewRear",
          "bottom": "viewBottom", "left": "viewLeft", "isometric": "viewIsometric", "iso": "viewIsometric"}


def t_take_screenshot(inp, env):
    if Gui is None or Gui.ActiveDocument is None:
        raise ValueError("No active 3D view.")
    view = Gui.ActiveDocument.ActiveView
    v = (inp.get("view") or "").lower().strip()
    if v:
        m = _VIEWS.get(v)
        if not m:
            raise ValueError(f"Unknown view '{v}'.")
        getattr(view, m)()
    if inp.get("fit", True):
        try:
            view.fitAll()
        except Exception:
            pass
    w = int(inp.get("width") or 800)
    h = int(inp.get("height") or 600)
    fd, path = tempfile.mkstemp(prefix="freegad_", suffix=".png")
    os.close(fd)
    try:
        Gui.updateGui()
        view.saveImage(path, w, h, "White")
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
    cam = ""
    try:
        cam = view.getCameraType()
    except Exception:
        pass
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
        {"type": "text", "text": f"Screenshot of the active view ({w}x{h}, {cam} camera)."},
    ]


def t_measure(inp, env):
    doc = _doc(env)
    oa, sa = _split_ref(doc, inp["a"])
    sha = _subshape(oa, sa)
    res = {"a": inp["a"], "a_info": context.shape_summary(sha, detailed=False)}
    try:
        if sha.ShapeType == "Face":
            res["a_info"]["center_of_mass"] = context.vec(sha.CenterOfMass)
            res["a_info"]["surface"] = type(sha.Surface).__name__
            try:
                res["a_info"]["normal_at_center"] = context.vec(sha.normalAt(0, 0))
            except Exception:
                pass
            if hasattr(sha.Surface, "Radius"):
                res["a_info"]["radius"] = context._r(sha.Surface.Radius)
        elif sha.ShapeType == "Edge":
            res["a_info"]["start"] = context.vec(sha.Vertexes[0].Point)
            res["a_info"]["end"] = context.vec(sha.Vertexes[-1].Point)
            res["a_info"]["curve"] = type(sha.Curve).__name__
            if hasattr(sha.Curve, "Radius"):
                res["a_info"]["radius"] = context._r(sha.Curve.Radius)
                res["a_info"]["center"] = context.vec(sha.Curve.Center)
        elif sha.ShapeType == "Vertex":
            res["a_info"]["point"] = context.vec(sha.Point)
    except Exception as ex:
        res["a_detail_error"] = str(ex)
    if inp.get("b"):
        ob, sb = _split_ref(doc, inp["b"])
        shb = _subshape(ob, sb)
        d, pts, _ = sha.distToShape(shb)
        res["b"] = inp["b"]
        res["distance"] = context._r(d)
        if pts:
            res["closest_points"] = [context.vec(pts[0][0]), context.vec(pts[0][1])]
        try:
            ca, cb = sha.CenterOfMass, shb.CenterOfMass
            res["center_delta"] = context.vec(cb - ca)
        except Exception:
            pass
    return _j(res)


def _short(val, maxlen=2000):
    try:
        if hasattr(val, "Name") and hasattr(val, "TypeId"):
            return f"<{val.Name}>"
        if hasattr(val, "ShapeType"):
            return context.shape_summary(val)
        s = repr(val)
    except Exception as ex:
        return f"<unprintable: {ex}>"
    return s if len(s) <= maxlen else s[:maxlen] + "..."


def _snapshot_names(doc):
    return {o.Name for o in doc.Objects}


def _doc_delta(doc, before):
    after = _snapshot_names(doc)
    added = sorted(after - before)
    removed = sorted(before - after)
    errs = []
    for o in doc.Objects:
        try:
            if "Invalid" in o.State:
                errs.append(o.Name)
        except Exception:
            pass
    return {"added": added, "removed": removed, "objects_in_error": errs}


# Time limit and memory guard are user settings (config.script_timeout, config.memory_guard);
# the defaults below apply when no config is available.
RUN_PYTHON_TIMEOUT_S = 120    # scripts over this budget are aborted with TimeoutError; 0 = no limit
_PUMP_EVERY_S = 0.5           # how often the GUI gets to repaint while a script runs
# Memory guard: a runaway script (per-point booleans, tessellating a whole assembly) can push
# FreeCAD to tens of GB of commit; Windows then pages so hard the desktop stops responding and
# only a reboot helps (seen: freecad.exe at 86 GB virtual on a 24 GB box). Abort well before that.
MEM_ABORT_GROWTH_FRAC = 0.5   # abort when the script has grown the process by this share of RAM
MEM_ABORT_AVAIL_FRAC = 0.08   # ...or when free physical RAM drops below this share of RAM
MEM_ABORT_AVAIL_MIN_MB = 1536 # ...or below this absolute amount
_LAST_RUN_STATS = None        # memory/timing stats of the last run_python, for telemetry


def pop_run_stats():
    """Per-tool stats recorded by the last run_python (or None); cleared on read."""
    global _LAST_RUN_STATS
    st, _LAST_RUN_STATS = _LAST_RUN_STATS, None
    return st


class _Watchdog:
    """Trace hook active while run_python executes on the GUI thread: aborts scripts that
    exceed the time or memory budget and pumps paint events so FreeCAD never looks frozen.
    All of it acts between Python lines only - a single long OCC call can still block (or
    allocate) until it returns."""

    def __init__(self, timeout_s, memory_guard=True):
        self.timeout_s = timeout_s
        self.deadline = (time.time() + timeout_s) if timeout_s else None     # None = no time limit
        self.memory_guard = memory_guard
        self.next_pump = 0.0
        st = telemetry.mem_status() or {}
        self.proc0 = st.get("proc_mb") or 0
        self.total_mb = st.get("total_mb") or 0
        self.mem_peak = self.proc0
        self.mem_avail_min = st.get("avail_mb")
        self.mem_abort = False
        self.abort_msg = None

    def check_memory(self):
        st = telemetry.mem_status()
        if not st:
            return
        proc = st.get("proc_mb") or 0
        avail = st.get("avail_mb")
        total = st.get("total_mb") or self.total_mb
        self.mem_peak = max(self.mem_peak, proc)
        if avail is not None:
            self.mem_avail_min = avail if self.mem_avail_min is None else min(self.mem_avail_min, avail)
        growth = proc - self.proc0
        too_big = total and growth > total * MEM_ABORT_GROWTH_FRAC
        too_low = avail is not None and total and avail < max(MEM_ABORT_AVAIL_MIN_MB, total * MEM_ABORT_AVAIL_FRAC)
        if too_big or too_low:
            self.mem_abort = True
            self.abort_msg = (
                "Script aborted by the memory guard: FreeCAD grew by %d MB (now %d MB) and the machine "
                "has %s MB of %d MB RAM free - continuing would freeze the whole PC. Work done so far is "
                "kept (one undo step). Do the job with far less geometry: query BoundBox / distToShape / "
                "a single section instead of booleans in a loop, operate on one simplified solid "
                "(shape.copy() of just the part you need, removeSplitter()), avoid tessellating or "
                "fusing whole assemblies, and free big intermediate shapes (del) between steps."
                % (growth, proc, "?" if avail is None else avail, total))
            raise MemoryError(self.abort_msg)

    def stats(self):
        st = telemetry.mem_status() or {}
        proc = st.get("proc_mb")
        return {"mem_delta_mb": (proc - self.proc0) if proc is not None else None,
                "mem_peak_mb": max(self.mem_peak, proc or 0),
                "mem_avail_min_mb": self.mem_avail_min, "mem_abort": self.mem_abort}

    def trace(self, frame, event, arg):
        if event == "line":
            if self.mem_abort:          # sticky: a script's own `except Exception` can't resume
                raise MemoryError(self.abort_msg)
            now = time.time()
            if self.deadline is not None and now > self.deadline:
                raise TimeoutError(
                    "Script exceeded the %d s budget and was aborted (work done so far is kept, "
                    "one undo step). Use cheaper geometry queries (BoundBox, distToShape, a single "
                    "slice with several planes) instead of per-point boolean loops, sample fewer "
                    "points, or split the job into smaller run_python calls." % self.timeout_s)
            if now >= self.next_pump:
                self.next_pump = now + _PUMP_EVERY_S
                if self.memory_guard:
                    self.check_memory()
                if QtWidgets is not None:
                    try:
                        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
                    except Exception:
                        pass
        return self.trace


def t_run_python(inp, env):
    code = inp.get("code") or ""
    purpose = inp.get("purpose") or "Run Python in FreeCAD"
    if not code.strip():
        raise ValueError("code is empty.")
    if not _confirm(env, purpose, code):
        return "User declined to run this code. Ask them what they'd prefer instead."

    doc = App.ActiveDocument if App.ActiveDocument else env.get("doc")
    ns = {"App": App, "FreeCAD": App, "doc": doc, "__name__": "__freegad__"}
    if Gui is not None:
        ns["Gui"] = Gui
        ns["FreeCADGui"] = Gui
    for mod in ("Part", "Sketcher", "Draft", "Mesh"):
        try:
            ns[mod] = __import__(mod)
        except Exception:
            pass

    before = _snapshot_names(doc) if doc else set()
    buf = io.StringIO()
    result = {"purpose": purpose}
    if doc:
        doc.openTransaction("FreeGAD: " + purpose[:60])
    ok = True
    t0 = time.time()
    cfg = env.get("config")
    wd = _Watchdog(RUN_PYTHON_TIMEOUT_S if cfg is None else cfg.script_timeout,
                   True if cfg is None else cfg.memory_guard)
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            old_trace = sys.gettrace()
            sys.settrace(wd.trace)
            try:
                # Try to evaluate the last line as an expression so its value is returned.
                lines = code.rstrip().split("\n")
                exec(compile("\n".join(lines[:-1]) + "\n", "<freegad>", "exec"), ns) if len(lines) > 1 else None
                try:
                    val = eval(compile(lines[-1].strip(), "<freegad>", "eval"), ns)
                    if val is not None:
                        result["value"] = _short(val)
                except SyntaxError:
                    exec(compile(lines[-1] + "\n", "<freegad>", "exec"), ns)
            except Exception:
                ok = False
                result["traceback"] = traceback.format_exc(limit=6)
            finally:
                sys.settrace(old_trace)
        if wd.mem_abort:
            ns.clear()                  # drop the script's references to big shapes
            gc.collect()
        if doc:
            try:
                doc.recompute()
            except Exception as ex:
                result["recompute_error"] = str(ex)
    finally:
        if doc:
            doc.commitTransaction()
        if Gui is not None:
            try:
                Gui.updateGui()
            except Exception:
                pass
        global _LAST_RUN_STATS
        _LAST_RUN_STATS = wd.stats()
    result["seconds"] = round(time.time() - t0, 1)
    if wd.mem_abort:
        result["memory_abort"] = True
    out = buf.getvalue()
    if out:
        result["stdout"] = out[-6000:]
    if doc:
        result.update(_doc_delta(doc, before))
    result["ok"] = ok
    s = _j(result)
    if not ok:
        raise RuntimeError(s)
    return s


def _parse_literal(text):
    import ast
    try:
        return ast.literal_eval(text)
    except Exception:
        return text  # plain string


def _set_path(obj, path, value):
    parts = path.split(".")
    if len(parts) == 1:
        setattr(obj, path, value)
        return
    # e.g. Placement.Base.x : fetch the top-level value, modify, write back (FreeCAD copies by value)
    top = getattr(obj, parts[0])
    cur = top
    for p in parts[1:-1]:
        cur = getattr(cur, p)
    setattr(cur, parts[-1], value)
    setattr(obj, parts[0], top)


def t_set_property(inp, env):
    doc = _doc(env)
    obj = _find(doc, inp["name"])
    prop = inp["property"]
    value = _parse_literal(str(inp["value"]))
    old = context.prop_value(obj, prop.split(".")[0])
    if not _confirm(env, f"Set {obj.Label}.{prop}", f"{obj.Name}.{prop}\n  old: {old}\n  new: {inp['value']}"):
        return "User declined the change."
    doc.openTransaction(f"FreeGAD: set {obj.Name}.{prop}")
    try:
        _set_path(obj, prop, value)
        doc.recompute()
    finally:
        doc.commitTransaction()
    return _j({"object": obj.Name, "property": prop, "old": old,
               "new": context.prop_value(obj, prop.split(".")[0]), "state": list(obj.State)})


def t_set_expression(inp, env):
    doc = _doc(env)
    obj = _find(doc, inp["name"])
    prop = inp["property"]
    expr = inp.get("expression") or ""
    if not _confirm(env, f"Expression on {obj.Label}.{prop}", f"{obj.Name}.{prop} = {expr or '(clear)'}"):
        return "User declined the change."
    doc.openTransaction(f"FreeGAD: expression {obj.Name}.{prop}")
    try:
        obj.setExpression(prop, expr if expr else None)
        doc.recompute()
    finally:
        doc.commitTransaction()
    return _j({"object": obj.Name, "property": prop, "expression": expr or None,
               "value": context.prop_value(obj, prop.split(".")[0]), "state": list(obj.State)})


def t_delete_object(inp, env):
    doc = _doc(env)
    names = inp.get("names") or []
    objs = [_find(doc, n) for n in names]
    deps = {}
    for o in objs:
        d = [x.Name for x in o.InList if x not in objs]
        if d:
            deps[o.Name] = d
    text = "Delete: " + ", ".join(o.Name for o in objs)
    if deps:
        text += "\nStill used by: " + _j(deps)
    if not _confirm(env, "Delete objects", text):
        return "User declined the deletion."
    deleted = [o.Name for o in objs]
    doc.openTransaction("FreeGAD: delete")
    try:
        for n in deleted:
            doc.removeObject(n)
        doc.recompute()
    finally:
        doc.commitTransaction()
    return _j({"deleted": deleted, "dependents_left": deps})


def t_set_visibility(inp, env):
    doc = _doc(env)
    if Gui is None:
        raise ValueError("No GUI.")
    gd = Gui.getDocument(doc.Name)
    done = []
    for n in inp.get("names") or []:
        o = _find(doc, n)
        gd.getObject(o.Name).Visibility = bool(inp.get("visible", True))
        done.append(o.Name)
    Gui.updateGui()
    return _j({"updated": done, "visible": bool(inp.get("visible", True))})


def t_select(inp, env):
    doc = _doc(env)
    if Gui is None:
        raise ValueError("No GUI.")
    Gui.Selection.clearSelection()
    done = []
    for ref in inp.get("refs") or []:
        o, sub = _split_ref(doc, ref)
        if sub:
            Gui.Selection.addSelection(doc.Name, o.Name, sub)
        else:
            Gui.Selection.addSelection(doc.Name, o.Name)
        done.append(ref)
    return _j({"selected": done})


def t_recompute(inp, env):
    doc = _doc(env)
    doc.recompute()
    errs = {}
    for o in doc.Objects:
        try:
            if "Invalid" in o.State:
                errs[o.Name] = list(o.State)
        except Exception:
            pass
    return _j({"recomputed": doc.Name, "objects_in_error": errs})


def _mem(env, scope):
    scope = (scope or "").lower()
    if scope == "user":
        return env["user_mem"]
    if scope in ("document", "drawing", "doc"):
        if env.get("doc_mem") is None:
            raise ValueError("No document memory: no document is open.")
        return env["doc_mem"]
    raise ValueError("scope must be 'document' or 'user'.")


def t_remember(inp, env):
    m = _mem(env, inp.get("scope"))
    e = m.add(inp.get("text"), inp.get("category"))
    try:
        from . import dm as _dm
        _dm.event("memory_used", {"scope": m.scope})     # count only; the note text never leaves the machine
    except Exception:
        pass
    return f"Saved to {m.scope} memory as [{e.id}]."


def t_forget(inp, env):
    m = _mem(env, inp.get("scope"))
    t = m.remove(inp.get("id"))
    return f"Forgot: {t}" if t else f"No note with id '{inp.get('id')}' in {m.scope} memory."


_EXEC = {
    "get_context": t_get_context,
    "list_documents": t_list_documents,
    "list_objects": t_list_objects,
    "get_object": t_get_object,
    "get_selection": t_get_selection,
    "take_screenshot": t_take_screenshot,
    "measure": t_measure,
    "run_python": t_run_python,
    "set_property": t_set_property,
    "set_expression": t_set_expression,
    "delete_object": t_delete_object,
    "set_visibility": t_set_visibility,
    "select": t_select,
    "recompute": t_recompute,
    "remember": t_remember,
    "forget": t_forget,
}
