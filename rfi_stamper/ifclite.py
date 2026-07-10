"""The Draw-In — IFC-lite import: thread an exchanged building model onto
Planloom's own frame (drawing-in is the weaving step that threads prepared
warp — someone else's work — into your loom).

Reads an IFC file (the open building-model exchange format, ISO 16739,
serialized as a STEP Physical File per ISO 10303-21) and places its
**walls, slabs and columns** as :class:`bim.Face` + :class:`bim.Segment`
geometry in the repo's world frame (x = East, y = North, z = up — the same
frame ``extrude.py`` targets).  Fully offline, stdlib + numpy only.

The professional-partial-importer contract this module keeps:

* **Never crash on unknown entities** — everything is indexed, only the
  needed closure is ever parsed; unknown types cost nothing.
* **Coverage stats, not silence** — every candidate product lands in
  ``report["imported"]`` or ``report["skipped"]`` (they sum to the candidate
  count), with per-type ``unsupported_counts`` for geometry we don't do.
* **Units first** — SI-prefix and conversion-based (FOOT/INCH) length units
  resolve before any geometry; one uniform scale applied ONCE to final
  vertices (scaling dims and placements separately double-scales).
* **'Body' over 'Axis'** — a wall carries both; the wrong pick imports a
  stick figure.  No 'Body' representation → an honest skip.
* **Schema tolerance** — IFC2X3 and IFC4 share the leading seven product
  attributes; nothing here indexes past position 6 on a product, so both
  schemas take one code path.

Honest SKIP list (each lands in the report by name): boolean/clipping
results and openings (CSG kernel territory — walls import without door
holes), BReps and tessellations, curved profiles/sweeps beyond circles,
materials/styles, property sets (the free product ``Name`` is the only
metadata), georeferencing, non-uniform transforms.  Import only — Planloom
never writes IFC.
"""
from __future__ import annotations

import math
import re
import zipfile
from typing import NamedTuple

import numpy as np

from . import bim

SIZE_CAP = 200 * 1024 * 1024        # refuse larger files loudly
MAX_PRODUCTS = 5000                 # candidate cap (logged, never silent)
_FT_PER_M = 1.0 / 0.3048

#: product class -> viewer system (extension = one line here)
PRODUCT_TABLE = {"IFCWALL": "walls", "IFCWALLSTANDARDCASE": "walls",
                 "IFCSLAB": "slabs", "IFCCOLUMN": "columns"}
_SYSTEM_COLORS = {"walls": "#9aab9e",       # matches extrude's wall green
                  "slabs": "#8f9aa8", "columns": "#a09080"}
_CIRCLE_SIDES = 16                  # circle profile -> 16-gon

_SI_PREFIX = {"EXA": 1e18, "PETA": 1e15, "TERA": 1e12, "GIGA": 1e9,
              "MEGA": 1e6, "KILO": 1e3, "HECTO": 1e2, "DECA": 1e1,
              "DECI": 1e-1, "CENTI": 1e-2, "MILLI": 1e-3, "MICRO": 1e-6,
              "NANO": 1e-9, "PICO": 1e-12}


class Ref(NamedTuple):
    """An instance reference ``#123`` inside an argument list."""
    id: int


class Enum(NamedTuple):
    """An enumeration value ``.LENGTHUNIT.`` / ``.T.`` / ``.MILLI.``."""
    name: str


class Typed(NamedTuple):
    """A typed (select-wrapped) value, e.g. ``IFCLENGTHMEASURE(0.3048)``."""
    name: str
    args: list


class _Skip(Exception):
    """Internal: convert one product's failure into a skip reason."""


# --------------------------------------------------------------------------- #
#  Pass 1 — the string/comment-aware record indexer                            #
# --------------------------------------------------------------------------- #

_HEAD = re.compile(r"\s*#(\d+)\s*=\s*([A-Z0-9_]*)\s*\(")


def _scan_records(text: str) -> dict:
    """One O(n) pass -> ``{id: (TYPE, args_pos)}`` (args_pos = index of '(').

    A state machine (in-string / in-comment) finds top-level ``;``
    terminators — strings legally contain ``;()#,`` and ``''`` is an escaped
    quote, so any split-on-semicolon approach mis-indexes.  Only the record
    HEAD is matched here; arguments parse lazily on demand (pass 2), so
    unknown entities and forward references are free.
    """
    idx: dict = {}
    n = len(text)
    i = 0
    start = 0
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if c == "'":
            in_str = True
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == ";":
            m = _HEAD.match(text, start, i)   # anchored: heads never hide
            if m:                             # inside a leading string
                rid = int(m.group(1))
                typ = m.group(2)          # empty for complex (A()B()) records
                idx[rid] = (typ, m.end() - 1)
            start = i + 1
        i += 1
    return idx


# --------------------------------------------------------------------------- #
#  Pass 2 — the recursive-descent argument parser (lazy, memoized)             #
# --------------------------------------------------------------------------- #

_CODEPAGES = {c: f"iso-8859-{k}" for k, c in enumerate("ABCDEFGHI", 1)}


def _decode_string(raw: str) -> str:
    """Decode one STEP string body (``''`` already collapsed by the lexer).

    Handles ``\\\\``, ``\\S\\c`` (+ ``\\PA\\``… code-page selects), ``\\X\\hh``,
    ``\\X2\\…\\X0\\`` (UTF-16BE) and ``\\X4\\…\\X0\\`` (UTF-32BE).  Raw bytes
    ≥ 0x80 are non-conforming but common (exporters dump UTF-8 straight in):
    tolerance rule — attempt a UTF-8 re-decode, fall back to as-is.
    """
    out: list = []
    page = "iso-8859-1"
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if raw.startswith("\\\\", i):
            out.append("\\")
            i += 2
        elif raw.startswith("\\S\\", i) and i + 3 < n:
            out.append(bytes([(ord(raw[i + 3]) + 128) & 0xFF]).decode(
                page, "replace"))
            i += 4
        elif raw.startswith("\\P", i) and i + 3 < n and raw[i + 3] == "\\":
            page = _CODEPAGES.get(raw[i + 2], "iso-8859-1")
            i += 4
        elif raw.startswith("\\X2\\", i):
            j = raw.find("\\X0\\", i + 4)
            j = n if j < 0 else j
            hexs = raw[i + 4:j]
            try:
                out.append(bytes.fromhex(hexs).decode("utf-16-be", "replace"))
            except ValueError:
                out.append(hexs)
            i = min(j + 4, n)
        elif raw.startswith("\\X4\\", i):
            j = raw.find("\\X0\\", i + 4)
            j = n if j < 0 else j
            hexs = raw[i + 4:j]
            try:
                out.append(bytes.fromhex(hexs).decode("utf-32-be", "replace"))
            except ValueError:
                out.append(hexs)
            i = min(j + 4, n)
        elif raw.startswith("\\X\\", i) and i + 4 < n:
            try:
                out.append(bytes.fromhex(raw[i + 3:i + 5]).decode(
                    "iso-8859-1"))
            except ValueError:
                out.append(raw[i + 3:i + 5])
            i += 5
        else:
            out.append(c)
            i += 1
    s = "".join(out)
    if any(ch >= "\x80" for ch in s):
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    return s


class _Parser:
    """Recursive descent over one record's argument list."""

    def __init__(self, text: str, pos: int):
        self.t = text
        self.i = pos

    def _ws(self):
        t, n = self.t, len(self.t)
        while self.i < n:
            c = t[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "/" and t.startswith("/*", self.i):
                j = t.find("*/", self.i + 2)
                self.i = n if j < 0 else j + 2
            else:
                return

    def parse_list(self) -> list:
        """At '(' -> the parsed argument list, cursor past the ')'."""
        assert self.t[self.i] == "("
        self.i += 1
        out: list = []
        while True:
            self._ws()
            c = self.t[self.i]
            if c == ")":
                self.i += 1
                return out
            if c == ",":
                self.i += 1
                continue
            out.append(self._value())

    def _value(self):
        t = self.t
        c = t[self.i]
        if c == "$":
            self.i += 1
            return None
        if c == "*":
            self.i += 1
            return "*"
        if c == "(":
            return self.parse_list()
        if c == "#":
            j = self.i + 1
            while t[j].isdigit():
                j += 1
            ref = Ref(int(t[self.i + 1:j]))
            self.i = j
            return ref
        if c == "'":
            j = self.i + 1
            parts: list = []
            while True:
                k = t.index("'", j)
                if k + 1 < len(t) and t[k + 1] == "'":
                    parts.append(t[j:k + 1])       # '' -> one literal quote
                    j = k + 2
                    continue
                parts.append(t[j:k])
                self.i = k + 1
                return _decode_string("".join(parts))
        if c == ".":
            j = t.index(".", self.i + 1)
            e = Enum(t[self.i + 1:j])
            self.i = j + 1
            return e
        if c == '"':                                # binary — never used here
            j = t.index('"', self.i + 1)
            b = t[self.i + 1:j]
            self.i = j + 1
            return Typed("BINARY", [b])
        if c.isdigit() or c in "+-":
            j = self.i
            while j < len(t) and (t[j].isdigit() or t[j] in "+-.Ee"):
                j += 1
            tok = t[self.i:j]
            self.i = j
            return float(tok) if any(x in tok for x in ".Ee") else int(tok)
        # keyword -> typed value, e.g. IFCLENGTHMEASURE(0.3048)
        j = self.i
        while j < len(t) and (t[j].isalnum() or t[j] == "_"):
            j += 1
        name = t[self.i:j]
        self.i = j
        self._ws()
        return Typed(name, self.parse_list())


class _File:
    """Lazy accessor: type + memoized parsed args per instance id."""

    def __init__(self, text: str, index: dict):
        self.text = text
        self.index = index
        self._memo: dict = {}

    def type_of(self, ref) -> str:
        ent = self.index.get(ref.id if isinstance(ref, Ref) else ref)
        return ent[0] if ent else ""

    def args(self, ref) -> list:
        rid = ref.id if isinstance(ref, Ref) else ref
        if rid in self._memo:
            return self._memo[rid]
        ent = self.index.get(rid)
        if ent is None:
            raise _Skip(f"dangling reference #{rid}")
        out = _Parser(self.text, ent[1]).parse_list()
        self._memo[rid] = out
        return out


def _num(v) -> float:
    """A numeric argument, unwrapping one typed-value (select) level."""
    if isinstance(v, Typed) and len(v.args) == 1:
        v = v.args[0]
    if not isinstance(v, (int, float)):
        raise _Skip(f"expected a number, got {v!r}")
    return float(v)


# --------------------------------------------------------------------------- #
#  Units (§ do this first)                                                     #
# --------------------------------------------------------------------------- #

def _si_metres(F: _File, ref) -> float:
    a = F.args(ref)                     # (Dimensions=*, UnitType, Prefix, Name)
    prefix = a[2]
    return _SI_PREFIX.get(prefix.name, 1.0) if isinstance(prefix, Enum) \
        else 1.0


def _length_scale(F: _File) -> tuple:
    """Metres per project length unit + warnings; never raises."""
    warns: list = []
    for rid in sorted(F.index):
        if F.type_of(rid) == "IFCPROJECT":
            try:
                ua = F.args(rid)[8]
                for u in F.args(ua)[0]:
                    t = F.type_of(u)
                    a = F.args(u)
                    kind = a[1]
                    if not (isinstance(kind, Enum)
                            and kind.name == "LENGTHUNIT"):
                        continue
                    if t == "IFCSIUNIT":
                        return _si_metres(F, u), warns
                    if t == "IFCCONVERSIONBASEDUNIT":
                        mwu = F.args(a[3])      # (ValueComponent, UnitComponent)
                        return _num(mwu[0]) * _si_metres(F, mwu[1]), warns
            except (_Skip, IndexError, TypeError, AttributeError):
                break
    warns.append("no usable length unit; assuming metres")
    return 1.0, warns


# --------------------------------------------------------------------------- #
#  Placement math                                                              #
# --------------------------------------------------------------------------- #

def _vec(F: _File, ref, dim: int = 3) -> np.ndarray:
    """IfcCartesianPoint / IfcDirection -> padded float vector."""
    coords = F.args(ref)[0]
    v = np.zeros(dim)
    for k, c in enumerate(coords[:dim]):
        v[k] = _num(c)
    return v


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        raise _Skip("zero-length direction")
    return v / n


def _axis2placement3d(F: _File, ref) -> np.ndarray:
    """IfcAxis2Placement3D -> 4x4.  Gram-Schmidt on RefDirection: exporters
    emit slightly-off vectors, and without it the rotation shears subtly."""
    if ref is None:
        return np.eye(4)
    a = F.args(ref)                     # (Location, Axis, RefDirection)
    loc = _vec(F, a[0]) if a[0] is not None else np.zeros(3)
    Z = _unit(_vec(F, a[1])) if len(a) > 1 and a[1] is not None \
        else np.array([0.0, 0.0, 1.0])
    x0 = _vec(F, a[2]) if len(a) > 2 and a[2] is not None \
        else np.array([1.0, 0.0, 0.0])
    X = None
    for cand in (x0, np.array([1.0, 0, 0]), np.array([0, 1.0, 0]),
                 np.array([0, 0, 1.0])):
        p = cand - float(cand @ Z) * Z
        if float(np.linalg.norm(p)) > 1e-9:
            X = p / float(np.linalg.norm(p))
            break
    Y = np.cross(Z, X)
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = X, Y, Z, loc
    return M


def _axis2placement2d(F: _File, ref) -> tuple:
    """IfcAxis2Placement2D -> (origin2, X2, Y2)."""
    if ref is None:
        return np.zeros(2), np.array([1.0, 0.0]), np.array([0.0, 1.0])
    a = F.args(ref)
    loc = _vec(F, a[0], 2) if a[0] is not None else np.zeros(2)
    X = _unit(_vec(F, a[1], 2)) if len(a) > 1 and a[1] is not None \
        else np.array([1.0, 0.0])
    return loc, X, np.array([-X[1], X[0]])


def _placement(F: _File, ref, memo: dict, stack: set) -> np.ndarray:
    """IfcLocalPlacement chain -> composed 4x4 (memoized, cycle-guarded)."""
    if ref is None:
        return np.eye(4)
    if ref.id in memo:
        return memo[ref.id]
    if ref.id in stack:
        raise _Skip("placement cycle")
    stack.add(ref.id)
    try:
        t = F.type_of(ref)
        if t == "IFCLOCALPLACEMENT":
            a = F.args(ref)             # (PlacementRelTo, RelativePlacement)
            parent = _placement(F, a[0], memo, stack)
            rel = a[1]
            if rel is not None and F.type_of(rel) == "IFCAXIS2PLACEMENT2D":
                o, X, Y = _axis2placement2d(F, rel)
                M = np.eye(4)
                M[:2, 0], M[:2, 1], M[:2, 3] = X, Y, o
            else:
                M = _axis2placement3d(F, rel)
            out = parent @ M
        else:
            out = _axis2placement3d(F, ref)
    finally:
        stack.discard(ref.id)
    memo[ref.id] = out
    return out


def _world_context(F: _File) -> np.ndarray:
    """The model context's WorldCoordinateSystem (usually identity — but
    silently ignoring a non-identity WCS shifts the whole model)."""
    for rid in sorted(F.index):
        if F.type_of(rid) == "IFCGEOMETRICREPRESENTATIONCONTEXT":
            try:                        # attr 4 = WorldCoordinateSystem
                return _axis2placement3d(F, F.args(rid)[4])
            except (_Skip, IndexError):
                return np.eye(4)
    return np.eye(4)


def _xform_operator(F: _File, ref) -> np.ndarray:
    """IfcCartesianTransformationOperator3D -> 4x4 (uniform scale only)."""
    a = F.args(ref)                 # (Axis1, Axis2, LocalOrigin, Scale, Axis3)
    Z = _unit(_vec(F, a[4])) if len(a) > 4 and a[4] is not None \
        else np.array([0.0, 0.0, 1.0])
    x0 = _vec(F, a[0]) if a[0] is not None else np.array([1.0, 0.0, 0.0])
    X = None
    for cand in (x0, np.array([1.0, 0, 0]), np.array([0, 1.0, 0]),
                 np.array([0, 0, 1.0])):
        p = cand - float(cand @ Z) * Z
        if float(np.linalg.norm(p)) > 1e-9:
            X = p / float(np.linalg.norm(p))
            break
    Y = _unit(_vec(F, a[1])) if len(a) > 1 and a[1] is not None \
        else np.cross(Z, X)
    o = _vec(F, a[2]) if len(a) > 2 and a[2] is not None else np.zeros(3)
    s = _num(a[3]) if len(a) > 3 and a[3] is not None else 1.0
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2] = X * s, Y * s, Z * s
    M[:3, 3] = o
    return M


# --------------------------------------------------------------------------- #
#  Profiles + sweep                                                            #
# --------------------------------------------------------------------------- #

def _curve_ring(F: _File, ref, unsupported: dict) -> list:
    t = F.type_of(ref)
    if t == "IFCPOLYLINE":
        pts = [tuple(_vec(F, p, 2)) for p in F.args(ref)[0]]
    elif t == "IFCINDEXEDPOLYCURVE":
        a = F.args(ref)                 # (Points, Segments, SelfIntersect)
        coords = [tuple(float(_num(c)) for c in xy[:2])
                  for xy in F.args(a[0])[0]]
        segs = a[1] if len(a) > 1 else None
        if segs is None:
            pts = list(coords)
        else:
            pts = []
            for s in segs:
                if not (isinstance(s, Typed) and s.name == "IFCLINEINDEX"):
                    name = s.name if isinstance(s, Typed) else "?"
                    unsupported[name] = unsupported.get(name, 0) + 1
                    raise _Skip(f"curve segment {name} not supported")
                for iidx in s.args[0] if isinstance(s.args[0], list) \
                        else s.args:
                    p = coords[int(iidx) - 1]        # STEP indices are 1-based
                    if not pts or pts[-1] != p:
                        pts.append(p)
    else:
        unsupported[t] = unsupported.get(t, 0) + 1
        raise _Skip(f"curve {t} not supported")
    if len(pts) > 1 and all(abs(pts[0][k] - pts[-1][k]) < 1e-9
                            for k in (0, 1)):
        pts = pts[:-1]                  # drop the repeated closing point
    if len(pts) < 3:
        raise _Skip("degenerate profile ring")
    return pts


def _profile_ring(F: _File, ref, unsupported: dict) -> list:
    """SweptArea -> [(u, v), ...] in the profile plane."""
    t = F.type_of(ref)
    a = F.args(ref)
    if t == "IFCRECTANGLEPROFILEDEF":
        # CENTERED on its 2D Position: corners are ±half-dims, not (0,0)-
        # anchored — Position itself is optional ($) in IFC4
        o, X, Y = _axis2placement2d(F, a[2] if len(a) > 2 else None)
        hx, hy = _num(a[3]) / 2.0, _num(a[4]) / 2.0
        return [tuple(o + u * X + v * Y)
                for u, v in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]
    if t == "IFCCIRCLEPROFILEDEF":
        o, X, Y = _axis2placement2d(F, a[2] if len(a) > 2 else None)
        r = _num(a[3])
        return [tuple(o + r * math.cos(2 * math.pi * k / _CIRCLE_SIDES) * X
                      + r * math.sin(2 * math.pi * k / _CIRCLE_SIDES) * Y)
                for k in range(_CIRCLE_SIDES)]
    if t == "IFCARBITRARYCLOSEDPROFILEDEF":
        return _curve_ring(F, a[2], unsupported)
    unsupported[t] = unsupported.get(t, 0) + 1
    raise _Skip(f"profile {t} not supported")


def _sweep(F: _File, ref, unsupported: dict) -> tuple:
    """IfcExtrudedAreaSolid -> (verts Nx3 in solid-parent coords, face rings).

    ring -> bottom cap, extruded ring -> top cap, one side quad per edge.
    """
    a = F.args(ref)       # (SweptArea, Position, ExtrudedDirection, Depth)
    ring2 = _profile_ring(F, a[0], unsupported)
    M = _axis2placement3d(F, a[1] if len(a) > 1 else None)
    d3 = _unit(_vec(F, a[2]))
    depth = _num(a[3])
    n = len(ring2)
    bot = np.array([(M @ np.array([u, v, 0.0, 1.0]))[:3] for u, v in ring2])
    d = M[:3, :3] @ d3
    verts = np.vstack([bot, bot + d * depth])
    faces = [list(range(n)), list(range(n, 2 * n))]
    faces += [[k, (k + 1) % n, n + (k + 1) % n, n + k] for k in range(n)]
    return verts, faces


def _body_items(F: _File, prod_args, unsupported: dict) -> tuple:
    """Pick the 'Body' shape representation -> (items, extra-4x4 per item).

    Explicitly NOT 'Axis'/'FootPrint' (a wall carries both; the wrong pick
    imports a floor plan of sticks).  IfcMappedItem indirection is resolved
    one level: composed = MappingTarget ∘ MappingOrigin.
    """
    shape = prod_args[6]
    if shape is None:
        raise _Skip("no Representation")
    reps = F.args(shape)[2]
    body = None
    for r in reps:
        ident = F.args(r)[1]
        if ident == "Body":
            body = r
            break
    if body is None:
        for r in reps:
            ra = F.args(r)
            if ra[1] in ("Axis", "FootPrint"):
                continue            # a stick figure, even when swept-typed
            if ra[2] in ("SweptSolid", "Clipping", "MappedRepresentation"):
                body = r
                break
    if body is None:
        raise _Skip("no 'Body' representation")
    out: list = []
    for item in F.args(body)[3]:
        t = F.type_of(item)
        if t == "IFCMAPPEDITEM":
            a = F.args(item)            # (MappingSource, MappingTarget)
            tt = F.type_of(a[1])
            if tt != "IFCCARTESIANTRANSFORMATIONOPERATOR3D":
                unsupported[tt] = unsupported.get(tt, 0) + 1
                out.append((item, None, f"mapped-item target {tt}"))
                continue
            src = F.args(a[0])          # (MappingOrigin, MappedRepresentation)
            Mx = _xform_operator(F, a[1]) @ _axis2placement3d(F, src[0])
            for sub in F.args(src[1])[3]:
                out.append((sub, Mx, None))
        else:
            out.append((item, np.eye(4), None))
    return out


# --------------------------------------------------------------------------- #
#  bim mapping                                                                 #
# --------------------------------------------------------------------------- #

def _add_solid(model: bim.Model, verts: np.ndarray, faces: list, system: str):
    """Faces + shared-edge-deduped wireframe segments (the load_obj pattern)."""
    color = _SYSTEM_COLORS[system]
    vts = [tuple(float(c) for c in p) for p in verts]
    seen: set = set()
    order: list = []
    for ring in faces:
        model.faces.append(bim.Face([vts[k] for k in ring], color, system))
        for a, b in zip(ring, ring[1:] + ring[:1]):
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            if key not in seen:
                seen.add(key)
                order.append(key)
    for a, b in order:
        model.segments.append(bim.Segment(vts[a], vts[b], color, 1.0, system))


def _storeys(F: _File) -> list:
    """Storey names via IfcRelContainedInSpatialStructure (label-only —
    the placement chain already carries the storey transform, so files
    with broken containment still import)."""
    names: set = set()
    for rid in sorted(F.index):
        if F.type_of(rid) == "IFCRELCONTAINEDINSPATIALSTRUCTURE":
            try:
                st = F.args(rid)[5]
                if F.type_of(st) == "IFCBUILDINGSTOREY":
                    nm = F.args(st)[2]
                    if isinstance(nm, str) and nm:
                        names.add(nm)
            except (_Skip, IndexError):
                continue
    return sorted(names)


# --------------------------------------------------------------------------- #
#  Driver                                                                      #
# --------------------------------------------------------------------------- #

def _read_ifc_bytes(path: str) -> bytes:
    """Read the file (size-capped); ``.ifczip`` = a zip holding one .ifc —
    sniffed by magic bytes like ``core.read_document``, never the extension."""
    with open(path, "rb") as fh:
        data = fh.read(SIZE_CAP + 1)
    if len(data) > SIZE_CAP:
        raise ValueError(f"file exceeds the {SIZE_CAP // (1024 * 1024)} MB "
                         "import cap")
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist()
                     if n.lower().endswith(".ifc")] or z.namelist()
            if not names:
                raise ValueError("zip archive contains no members")
            data = z.read(names[0])
            if len(data) > SIZE_CAP:
                raise ValueError("zipped model exceeds the import cap")
    return data


_SCHEMA_RE = re.compile(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'")


def load_ifc(path: str, target_unit: str = "ft",
             max_products: int = MAX_PRODUCTS, log=print) -> tuple:
    """IFC building model -> ``(bim.Model, report)`` — walls/slabs/columns.

    ``target_unit`` is ``"ft"`` (decimal feet, the Fieldstitch/Loft world
    frame — the default) or ``"m"``.  IFC world axes are already E/N/up, so
    coordinates map onto bim x/y/z with NO flip; only the unit scale applies,
    once, to final vertices.

    The report is the honesty layer (deterministic; keys are a frozen
    contract): ``schema``, ``unit_scale``, ``target_unit``, ``imported``
    (per-system counts), ``skipped`` (``(id, ifc_class, reason)`` — every
    candidate lands here or in imported, never silently dropped),
    ``unsupported_counts``, ``storeys``, ``warnings``.  Raises
    ``ValueError`` only when ZERO products import, with the skip summary in
    the message.
    """
    if target_unit not in ("ft", "m"):
        raise ValueError(f"target_unit must be 'ft' or 'm', got {target_unit!r}")
    text = _read_ifc_bytes(path).decode("latin-1")
    m = _SCHEMA_RE.search(text)
    schema = m.group(1) if m else ""
    warnings: list = []
    if not schema:
        warnings.append("no FILE_SCHEMA header")
    elif not schema.upper().startswith(("IFC2X3", "IFC4")):
        warnings.append(f"unknown schema {schema!r}; attempting import")

    F = _File(text, _scan_records(text))
    metres, unit_warns = _length_scale(F)
    warnings.extend(unit_warns)
    unit_scale = metres * (_FT_PER_M if target_unit == "ft" else 1.0)
    wcs = _world_context(F)

    candidates = sorted(rid for rid in F.index
                        if F.type_of(rid) in PRODUCT_TABLE)
    if len(candidates) > max_products:
        warnings.append(f"{len(candidates)} candidate products; importing "
                        f"the first {max_products}")
        log(f"  · Draw-In cap: importing {max_products} of "
            f"{len(candidates)} products")
        candidates = candidates[:max_products]

    model = bim.Model()
    imported = {"walls": 0, "slabs": 0, "columns": 0}
    skipped: list = []
    unsupported: dict = {}
    pl_memo: dict = {}
    for pid in candidates:
        typ = F.type_of(pid)
        system = PRODUCT_TABLE[typ]
        try:
            pargs = F.args(pid)
            # every IfcProduct subtype in BOTH schemas shares the leading
            # seven attributes — never index past position 6 on a product
            M_obj = wcs @ _placement(F, pargs[5], pl_memo, set())
            got = 0
            reasons: list = []
            for item, Mx, why in _body_items(F, pargs, unsupported):
                if why is not None:
                    reasons.append(why)
                    continue
                t = F.type_of(item)
                if t != "IFCEXTRUDEDAREASOLID":
                    unsupported[t] = unsupported.get(t, 0) + 1
                    reasons.append(f"body item {t} not supported")
                    continue
                try:
                    verts, faces = _sweep(F, item, unsupported)
                except _Skip as e:
                    reasons.append(str(e))
                    continue
                hv = np.hstack([verts, np.ones((len(verts), 1))])
                world = ((M_obj @ Mx) @ hv.T).T[:, :3] * unit_scale
                _add_solid(model, world, faces, system)
                got += 1
            if not got:
                raise _Skip("; ".join(reasons) if reasons
                            else "empty representation")
            imported[system] += 1
        except _Skip as e:
            skipped.append((pid, typ, str(e)))
        except Exception as e:          # a malformed product never kills the
            skipped.append((pid, typ,   # import — it becomes a skip reason
                            f"{type(e).__name__}: {e}"))

    if not any(imported.values()):
        summary = "; ".join(f"#{i} {t}: {r}" for i, t, r in skipped[:8])
        raise ValueError(
            f"0 of {len(candidates)} products imported"
            + (f": {summary}" if summary
               else " (no walls/slabs/columns in file)"))
    model.systems = [(s, _SYSTEM_COLORS[s])
                     for s in ("walls", "slabs", "columns") if imported[s]]
    report = {"schema": schema, "unit_scale": unit_scale,
              "target_unit": target_unit, "imported": imported,
              "skipped": skipped,
              "unsupported_counts": dict(sorted(unsupported.items())),
              "storeys": _storeys(F), "warnings": warnings}
    return model, report
