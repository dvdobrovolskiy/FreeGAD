# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""Compact, prompt-friendly snapshots of the live FreeCAD document."""
import json
import os

import FreeCAD as App

try:
    import FreeCADGui as Gui
except Exception:  # headless
    Gui = None

MAX_OBJECTS = 150
MAX_SUBSHAPES = 30          # faces/edges listed per object in get_object


def _r(v, nd=3):
    try:
        return round(float(v), nd)
    except Exception:
        return v


def vec(v):
    return [_r(v.x), _r(v.y), _r(v.z)]


def placement(pl):
    try:
        ax = pl.Rotation.Axis
        return {
            "pos": vec(pl.Base),
            "axis": vec(ax),
            "angle_deg": _r(pl.Rotation.Angle * 180.0 / 3.141592653589793),
        }
    except Exception:
        return None


def shape_summary(shape, detailed=False):
    if shape is None:
        return None
    try:
        if shape.isNull():
            return {"null": True}
    except Exception:
        return None
    s = {}
    try:
        s["type"] = shape.ShapeType
        s["valid"] = bool(shape.isValid())
        bb = shape.BoundBox
        s["bbox"] = {"min": [_r(bb.XMin), _r(bb.YMin), _r(bb.ZMin)],
                     "max": [_r(bb.XMax), _r(bb.YMax), _r(bb.ZMax)],
                     "size": [_r(bb.XLength), _r(bb.YLength), _r(bb.ZLength)]}
        s["solids"] = len(shape.Solids)
        s["faces"] = len(shape.Faces)
        s["edges"] = len(shape.Edges)
        s["vertexes"] = len(shape.Vertexes)
        if shape.Solids:
            s["volume"] = _r(shape.Volume, 2)
        if shape.Faces:
            s["area"] = _r(shape.Area, 2)
        if shape.Edges and not shape.Faces:
            s["length"] = _r(shape.Length, 2)
        if detailed:
            try:
                c = shape.CenterOfMass
                s["center_of_mass"] = vec(c)
            except Exception:
                pass
            faces = []
            for i, f in enumerate(shape.Faces[:MAX_SUBSHAPES]):
                try:
                    faces.append({"name": f"Face{i+1}", "surface": type(f.Surface).__name__,
                                  "area": _r(f.Area, 2), "center": vec(f.CenterOfMass)})
                except Exception:
                    pass
            s["face_list"] = faces
            if len(shape.Faces) > MAX_SUBSHAPES:
                s["face_list_truncated"] = len(shape.Faces) - MAX_SUBSHAPES
            edges = []
            for i, e in enumerate(shape.Edges[:MAX_SUBSHAPES]):
                try:
                    edges.append({"name": f"Edge{i+1}", "curve": type(e.Curve).__name__,
                                  "length": _r(e.Length, 2)})
                except Exception:
                    pass
            s["edge_list"] = edges
            if len(shape.Edges) > MAX_SUBSHAPES:
                s["edge_list_truncated"] = len(shape.Edges) - MAX_SUBSHAPES
    except Exception as ex:
        s["error"] = str(ex)
    return s


def prop_value(obj, name, maxlen=300):
    """Stringify one property value compactly."""
    try:
        v = getattr(obj, name)
    except Exception as ex:
        return f"<error: {ex}>"
    try:
        if hasattr(v, "Name") and hasattr(v, "TypeId") and hasattr(v, "Label"):
            return f"<{v.Name}>"
        if isinstance(v, (list, tuple)):
            out = []
            for item in v[:40]:
                if hasattr(item, "Name") and hasattr(item, "TypeId"):
                    out.append(f"<{item.Name}>")
                elif isinstance(item, (list, tuple)) and len(item) == 2 and hasattr(item[0], "Name"):
                    out.append(f"<{item[0].Name}>:{list(item[1])}")
                else:
                    out.append(str(item))
            s = "[" + ", ".join(out) + ("..." if len(v) > 40 else "") + "]"
            return s[:maxlen]
        if isinstance(v, App.Placement):
            return placement(v)
        if isinstance(v, App.Vector):
            return vec(v)
        if hasattr(v, "ShapeType"):
            return shape_summary(v)
        if hasattr(v, "Value") and hasattr(v, "UserString"):   # Quantity
            return str(v)            # locale-independent, e.g. "20 mm"
        s = str(v)
        return s if len(s) <= maxlen else s[:maxlen] + "..."
    except Exception as ex:
        return f"<unprintable: {ex}>"


def object_brief(obj):
    o = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    try:
        if Gui and Gui.ActiveDocument and obj.Document is App.ActiveDocument:
            vo = Gui.ActiveDocument.getObject(obj.Name)
            if vo is not None:
                o["visible"] = bool(vo.Visibility)
    except Exception:
        pass
    try:
        if obj.InList:
            o["used_by"] = [x.Name for x in obj.InList[:10]]
        if obj.OutList:
            o["depends_on"] = [x.Name for x in obj.OutList[:10]]
    except Exception:
        pass
    try:
        if hasattr(obj, "Shape"):
            sh = shape_summary(obj.Shape)
            if sh and not sh.get("null"):
                o["shape"] = {k: sh[k] for k in ("type", "bbox", "solids", "faces", "volume") if k in sh}
    except Exception:
        pass
    try:
        if hasattr(obj, "Placement"):
            pl = placement(obj.Placement)
            if pl and (pl["pos"] != [0, 0, 0] or pl["angle_deg"]):
                o["placement"] = pl
    except Exception:
        pass
    try:
        if obj.TypeId.startswith("Sketcher::"):
            o["geometry_count"] = obj.GeometryCount
            o["constraint_count"] = obj.ConstraintCount
            try:
                o["support"] = [x[0].Name for x in (obj.AttachmentSupport or obj.Support or [])]
            except Exception:
                pass
    except Exception:
        pass
    try:
        if obj.TypeId == "Spreadsheet::Sheet":
            cells = obj.PropertiesList
            o["cells"] = len([c for c in cells if len(c) <= 4 and c[0].isalpha() and c[1:].isdigit()])
    except Exception:
        pass
    try:
        eng = obj.ExpressionEngine
        if eng:
            o["expressions"] = {k: v for k, v in eng[:10]}
    except Exception:
        pass
    try:
        st = obj.State
        if "Invalid" in st or "Touched" in st:
            o["state"] = list(st)
    except Exception:
        pass
    return o


def object_full(obj):
    o = object_brief(obj)
    props = {}
    for p in obj.PropertiesList:
        if p in ("Shape", "Proxy"):
            continue
        try:
            ptype = obj.getTypeIdOfProperty(p)
        except Exception:
            ptype = "?"
        props[p] = {"type": ptype, "value": prop_value(obj, p)}
    o["properties"] = props
    try:
        if hasattr(obj, "Shape"):
            o["shape"] = shape_summary(obj.Shape, detailed=True)
    except Exception:
        pass
    try:
        if obj.TypeId.startswith("Sketcher::"):
            geo = []
            for i, g in enumerate(obj.Geometry[:80]):
                geo.append(f"{i}: {g}")
            o["geometry"] = geo
            cons = []
            for i, c in enumerate(obj.Constraints[:120]):
                cons.append(f"{i}: {c.Type} {c.Name or ''} first={c.First}/{c.FirstPos} "
                            f"second={c.Second}/{c.SecondPos} value={_r(c.Value)}".strip())
            o["constraints"] = cons
    except Exception:
        pass
    try:
        if obj.TypeId == "Spreadsheet::Sheet":
            cells = {}
            for p in obj.PropertiesList:
                if len(p) <= 5 and p[0].isalpha() and p[1:].isdigit():
                    try:
                        cells[p] = {"content": obj.getContents(p), "value": str(obj.get(p))}
                    except Exception:
                        pass
            o["cells"] = cells
    except Exception:
        pass
    return o


def selection_summary():
    out = []
    if Gui is None:
        return out
    try:
        for s in Gui.Selection.getSelectionEx():
            out.append({"doc": s.DocumentName, "object": s.ObjectName,
                        "subelements": list(s.SubElementNames)})
    except Exception:
        pass
    return out


def document_context(doc):
    """Compact snapshot for the system prompt."""
    if doc is None:
        return {"document": None, "note": "No document is open."}
    ctx = {}
    try:
        ctx["document"] = {"name": doc.Name, "label": doc.Label, "file": doc.FileName or "(unsaved)",
                           "uid": doc.Uid, "modified": bool(doc.Modified)}
    except Exception:
        ctx["document"] = {"name": doc.Name}
    try:
        ctx["freecad_version"] = ".".join(str(x) for x in App.Version()[:3])
    except Exception:
        pass
    try:
        ug = App.ParamGet("User parameter:BaseApp/Preferences/Units").GetInt("UserSchema", 0)
        ctx["unit_schema"] = ug
    except Exception:
        pass

    objs = doc.Objects
    counts = {}
    for o in objs:
        counts[o.TypeId] = counts.get(o.TypeId, 0) + 1
    ctx["object_count"] = len(objs)
    ctx["types"] = counts

    roots = [o for o in objs if not o.InList]
    ctx["root_objects"] = [o.Name for o in roots[:60]]

    listed = []
    for o in objs[:MAX_OBJECTS]:
        listed.append(object_brief(o))
    ctx["objects"] = listed
    if len(objs) > MAX_OBJECTS:
        ctx["objects_truncated"] = len(objs) - MAX_OBJECTS

    ctx["selection"] = selection_summary()
    try:
        if Gui and Gui.ActiveDocument and Gui.ActiveDocument.Document is doc:
            ctx["active_workbench"] = Gui.activeWorkbench().name()
    except Exception:
        pass
    return ctx


def context_text(doc):
    return json.dumps(document_context(doc), ensure_ascii=False, indent=1)


def open_documents():
    out = []
    for name, d in App.listDocuments().items():
        out.append({"name": name, "label": d.Label, "file": d.FileName or "(unsaved)",
                    "objects": len(d.Objects),
                    "active": App.ActiveDocument is d})
    return out
