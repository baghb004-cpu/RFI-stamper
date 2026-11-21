"""Architect dimension strings against the structure.  Brief 5.8, 6.

Each string is read with its two ENDS.  An end matches a structural line
when it sits within 1 1/2 in. of that line, measured along the dimension.
A string is then one of:

* ``same``       both ends on structural lines, and the printed value
                 agrees with the structure within 1/2 in.;
* ``different``  both ends on structural lines, the value does not agree;
* ``one``        only one end lands on a structural line;
* ``off``        neither end does (the string is not about the structure;
                 it is counted in the coverage statement, not judged).

Structural lines are the faces of concrete (every polygon edge), slab
edge runs, slab opening edges and gridlines.  An opening CENTERLINE is
not a structural line: a string that ties one is recorded as such, which
is how a jamb ends up "tied by the opening centerline only".
"""
from __future__ import annotations

import math

from . import model as M

SQUARE_DEG = 3.0


def structural_lines(level: M.Level) -> list:
    """-> ``[{"a", "b", "kind", "ref"}]`` in world feet."""
    out = []
    for g in level.grid.lines:
        out.append({"a": g.world_a, "b": g.world_b, "kind": "grid", "ref": g.name,
                    "infinite": True})
    for e in level.elements:
        if e.kind in (M.WALL, M.COLUMN, M.CONCRETE, M.SLAB_OPENING, M.MASONRY):
            n = len(e.poly)
            for i in range(n):
                out.append({"a": e.poly[i], "b": e.poly[(i + 1) % n],
                            "kind": "face" if e.kind != M.SLAB_OPENING else "opening",
                            "ref": e.id, "element": e})
        elif e.kind == M.SLAB_EDGE:
            for s in e.attrs.get("segments", []):
                if s["kind"] == "line":
                    out.append({"a": s["a"], "b": s["b"], "kind": "slab", "ref": e.id,
                                "element": e})
    return out


def _match_end(p, u, lines):
    """The structural line an end lands on, or None.  ``u`` is the unit
    direction of the dimension."""
    best = None
    lat = (-u[1], u[0])
    sp = p[0] * u[0] + p[1] * u[1]
    lp = p[0] * lat[0] + p[1] * lat[1]
    for ln in lines:
        a, b = ln["a"], ln["b"]
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy)
        if d < 1e-9:
            continue
        if abs((dx * u[0] + dy * u[1]) / d) > math.sin(math.radians(SQUARE_DEG)):
            continue                              # not square to the dimension
        s_line = ((a[0] + b[0]) / 2.0) * u[0] + ((a[1] + b[1]) / 2.0) * u[1]
        off = sp - s_line
        if abs(off) > M.ARCH_END_FT:
            continue
        if ln.get("infinite"):
            gap = 0.0
        else:
            la = a[0] * lat[0] + a[1] * lat[1]
            lb = b[0] * lat[0] + b[1] * lat[1]
            lo, hi = min(la, lb), max(la, lb)
            gap = 0.0 if lo <= lp <= hi else min(abs(lp - lo), abs(lp - hi))
        key = (gap, abs(off))
        if best is None or key < best[0]:
            best = (key, ln, off, s_line)
    if best is None:
        return None
    return {"line": best[1], "off_ft": best[2], "s": best[3], "gap_ft": best[0][0]}


def classify_strings(dims, frame: M.Frame, level: M.Level) -> list:
    """Page dimension strings (from ``dimread``) on the architect's sheet
    -> classified world strings:
    ``[{"text", "value_ft", "a", "b", "u", "cls", "ends": [match|None,
    match|None], "structure_ft", "delta_ft", "centerline": opening|None}]``."""
    lines = structural_lines(level)
    openings = level.of(M.WALL_OPENING)
    out = []
    for d in dims:
        a = frame.to_world(*d["a"])
        b = frame.to_world(*d["b"])
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln < 1e-6:
            continue
        u = ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)
        ends = [_match_end(a, u, lines), _match_end(b, u, lines)]
        row = {"text": d["text"], "value_ft": d["value_ft"], "a": a, "b": b, "u": u,
               "ends": ends, "structure_ft": None, "delta_ft": None, "centerline": None,
               "page_a": d["a"], "page_b": d["b"]}
        n_hit = sum(1 for e in ends if e is not None)
        if n_hit == 2:
            row["structure_ft"] = abs(ends[1]["s"] - ends[0]["s"])
            row["delta_ft"] = d["value_ft"] - row["structure_ft"]
            row["cls"] = "same" if abs(row["delta_ft"]) <= M.ARCH_SAME_FT else "different"
        elif n_hit == 1:
            row["cls"] = "one"
        else:
            row["cls"] = "off"
        # an unmatched end that sits on an opening centerline
        for k, p in enumerate((a, b)):
            if ends[k] is not None:
                continue
            for o in openings:
                ax = o.attrs["axis"]
                if abs(u[0] * ax[0] + u[1] * ax[1]) < math.cos(math.radians(SQUARE_DEG)):
                    continue
                c = o.attrs["center"]
                if abs((p[0] - c[0]) * ax[0] + (p[1] - c[1]) * ax[1]) <= M.ARCH_END_FT:
                    row["centerline"] = o
        out.append(row)
    return out


def end_status(sw_end: M.Element, strings) -> tuple:
    """How the architect ties one shear wall end or jamb:
    ``("string", row)`` a dimension string lands on its face;
    ``("centerline", row)`` only the opening centerline is tied;
    ``("none", None)``."""
    fa, fb = sw_end.attrs["face_a"], sw_end.attrs["face_b"]
    for row in strings:
        for m in row["ends"]:
            if m is None or m["line"].get("infinite"):
                continue
            a, b = m["line"]["a"], m["line"]["b"]
            if (_same(a, fa) and _same(b, fb)) or (_same(a, fb) and _same(b, fa)):
                return "string", row
    op = sw_end.attrs.get("opening")
    if op:
        op_id = op if isinstance(op, str) else op.id
        for row in strings:
            c = row["centerline"]
            if c is not None and (c is op or (c.id and c.id == op_id)):
                return "centerline", row
    return "none", None


def _same(p, q, tol: float = 1.0 * M.IN) -> bool:
    return math.hypot(p[0] - q[0], p[1] - q[1]) <= tol
