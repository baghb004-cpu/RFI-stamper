"""dxfread: the in-house ASCII DXF background reader (plus a small R12
background writer).

A CAD background is the second source the Batterboard checks a structural
plan against, and the frame its points are declared in.  This module turns
an **ASCII DXF** file into plain entities in DRAWING units -- lines,
polylines, circles, arcs, text and points, each with its layer -- and
writes the same subset back out (fixtures, and the per-level background
export).  No CAD library: the group-pair format is read here, in house.

What it reads
    HEADER ``$INSUNITS`` (the file's declared units), the LAYER table
    (name, color, off / frozen flags), BLOCKS, and from ENTITIES: LINE,
    LWPOLYLINE and POLYLINE + VERTEX (both normalize to kind
    ``"POLYLINE"``; per-vertex bulges are KEPT, not expanded -- see
    :func:`bulge_arc`), CIRCLE, ARC, TEXT and MTEXT (both kind ``"TEXT"``,
    inline formatting codes stripped), POINT, and INSERT.  An INSERT is
    expanded in place: block entities go through scale -> rotate ->
    translate about the block base point, nested up to
    :data:`MAX_INSERT_DEPTH`; an entity drawn on layer ``0`` inside a block
    takes the INSERT's layer; attribute values riding an INSERT are read
    as TEXT (that is where grid bubble letters usually live).

What it refuses
    Anything that is not ASCII DXF: a binary DXF, a native CAD drawing
    file, or any other file.  :func:`sniff` decides by magic bytes and
    first lines, never by the file extension, and :func:`read` raises
    :class:`DxfError` with :data:`NOT_ASCII_DXF` -- plain words the tab
    shows as is.  A structurally broken file (a group code with no value
    line, a group code that is not a whole number, a coordinate that is
    not a number) raises :class:`DxfError` naming the line.

Coverage-honest
    Every entity is either read or COUNTED in ``DxfDoc.skipped`` -- never
    silently dropped.  Keys are the entity type (``SPLINE``, ``HATCH``,
    ``DIMENSION``, ...) or a reason form: ``PAPERSPACE`` (group 67 = 1),
    ``INVISIBLE`` (group 60 = 1), ``INSERT(unresolved)`` (missing block,
    a block that inserts itself, or a nest deeper than the cap),
    ``INSERT(array)`` (row / column arrays), ``CIRCLE(nonuniform)`` /
    ``ARC(nonuniform)`` / ``POLYLINE(nonuniform)`` (a curve under an
    unequal x / y scale is no longer a circle -- skipped rather than
    emitted wrong), ``<TYPE>(tilted)`` / ``MTEXT(extruded)`` (see below),
    ``POLYLINE(mesh)``, ``ATTRIB(invisible)``, ``TEXT(empty)`` and
    ``<TYPE>(incomplete)`` (a required coordinate is missing; nothing is
    invented at the origin).

Rules a caller should know
    * 2D only: z is ignored.  Coordinates stay in drawing units;
      :meth:`DxfDoc.feet` gives feet per drawing unit from the DECLARED
      units and refuses to guess when the file declares none.
    * A planar entity stores its points in its OWN plane.  Mirroring in
      CAD leaves that plane flipped (extrusion 0,0,-1), where the stored
      x runs backwards: such an entity is brought back to plan
      coordinates (arcs stay counter-clockwise, bulges change sign).  One
      on a tilted plane is counted as ``<TYPE>(tilted)``, and multi-line
      text off the default plane as ``MTEXT(extruded)`` -- never placed
      by guesswork.
    * TEXT ``pts[0]`` is the point the drafter placed the text by: the
      alignment point for justified text (the stored lower-left corner is
      derived, and some writers leave it at the origin), else the
      insertion point.
    * ``block`` is the block path, outermost first (``"PAIR/COL"``).
    * Layer roles (:func:`classify_layers`) come from layer NAMES only.

The lenient pair reader in :mod:`rfi_stamper.selvage` skips what it cannot
parse (right for re-harvesting points); this reader is its strict twin and
reports the line instead.  Layer names written here must already satisfy
``selvage.validate_dxf_layer`` -- the rule is enforced at creation, never
silently fixed.

Fully offline: stdlib only.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .fsutil import atomic_write_bytes
from .selvage import validate_dxf_layer


class DxfError(ValueError):
    """A DXF could not be read; the message is plain words for the user."""


#: shown as is by the tab when the dropped CAD file is not ASCII DXF
NOT_ASCII_DXF = ("This CAD file is not an ASCII DXF. Please export the "
                 "background as ASCII DXF and drop it again.")

#: feet per drawing unit (international foot = 0.3048 m exactly)
FEET_PER_UNIT = {"in": 1.0 / 12.0, "ft": 1.0, "mm": 1.0 / 304.8,
                 "cm": 1.0 / 30.48, "m": 1.0 / 0.3048}
#: ``$INSUNITS`` codes this reader converts; any other code reads as
#: undeclared units (the code itself is kept in ``DxfDoc.insunits``)
INSUNITS = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}
_INSUNITS_CODE = {name: code for code, name in INSUNITS.items()}

#: the binary-DXF file signature: 22 bytes of format magic (18 characters
#: of text, then CR LF SUB NUL).  Kept as hex so the source carries no
#: product name.
BINARY_DXF_SIGNATURE = bytes.fromhex(
    "4175746f4341442042696e617279204458460d0a1a00")
#: a native CAD drawing file opens with this ASCII version tag
_NATIVE_CAD_TAG = b"AC10"

#: deepest INSERT nest that is expanded (a model-space INSERT is depth 1)
MAX_INSERT_DEPTH = 8

_SNIFF_BYTES = 65536


# ------------------------------------------------------------------ model --

@dataclass
class DxfEntity:
    """One entity in DRAWING units (see the module note for ``pts``)."""
    kind: str                 # LINE | POLYLINE | CIRCLE | ARC | TEXT | POINT
    layer: str
    pts: list = field(default_factory=list)
    closed: bool = False
    center: tuple | None = None
    radius: float | None = None
    start_deg: float | None = None    # ARC, counter-clockwise start -> end
    end_deg: float | None = None
    text: str = ""
    height: float = 0.0
    bulges: list = field(default_factory=list)   # POLYLINE: one per vertex
    block: str = ""           # block path, outermost first; "" = model space


@dataclass
class DxfDoc:
    path: str = ""
    units: str = ""           # "in" | "ft" | "mm" | "cm" | "m" | "" (none declared)
    insunits: int | None = None
    layers: dict = field(default_factory=dict)    # name -> {"color", "count", "off", "frozen"}
    entities: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)   # type / reason -> count
    extents: tuple | None = None                  # (xmin, ymin, xmax, ymax)

    def on(self, *layers) -> list:
        """Entities on any of these layers (names compare case-blind, the
        way CAD layer names do)."""
        want = {str(name).strip().upper() for name in layers}
        return [e for e in self.entities if e.layer.upper() in want]

    def feet(self, units: str | None = None) -> float:
        """Feet per drawing unit.

        The file's DECLARED units govern.  ``units`` is used only when the
        file declares none (a human answer, never a guess); with neither,
        :class:`DxfError`."""
        if self.units:
            return FEET_PER_UNIT[self.units]
        if units:
            key = str(units).strip().lower()
            if key not in FEET_PER_UNIT:
                raise DxfError(
                    f"Unknown units {units!r} for this CAD background. "
                    "Use one of: in, ft, mm, cm, m.")
            return FEET_PER_UNIT[key]
        if self.insunits not in (None, 0):
            raise DxfError(
                f"This CAD background declares unit code {self.insunits}, "
                "which is not inches, feet, millimeters, centimeters or "
                "meters. Tell the run which units the drawing uses (in, "
                "ft, mm, cm or m) and run again.")
        raise DxfError(
            "This CAD background declares no units. Tell the run which "
            "units the drawing uses (in, ft, mm, cm or m) and run again.")


# ------------------------------------------------------------------ sniff --

def _normalize(text: str) -> str:
    """One line end for the whole text, whatever the file used (CR LF, LF,
    CR, and the doubled-CR damage a bad text-mode copy leaves)."""
    return text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")


class _Lines:
    """Lazy line reader over normalized text.  A background can run to
    millions of lines, so they are never all held at once.  ``ln`` is the
    1-based number of the line last returned, ``pos`` the offset of the
    next one (a section can be re-read from a remembered ``pos``).  A
    value line may be legitimately empty, so nothing is dropped except
    the empty tail after the final line end."""

    def __init__(self, text: str, pos: int = 0, ln: int = 0):
        self.text, self.pos, self.ln, self.n = text, pos, ln, len(text)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        if self.pos >= self.n:
            raise StopIteration
        j = self.text.find("\n", self.pos)
        if j < 0:
            line, self.pos = self.text[self.pos:], self.n
        else:
            line, self.pos = self.text[self.pos:j], j + 1
        self.ln += 1
        return line


def sniff(path: str) -> str:
    """``"ascii_dxf"`` | ``"binary_dxf"`` | ``"native_cad"`` | ``"other"``.

    Decided by content only: the binary-DXF signature, the native drawing
    version tag, or text whose first group pair (after any leading 999
    comments) is ``0`` / ``SECTION``.  The extension is never trusted."""
    with open(path, "rb") as f:
        head = f.read(_SNIFF_BYTES)
    if head.startswith(BINARY_DXF_SIGNATURE):
        return "binary_dxf"
    if head.startswith(_NATIVE_CAD_TAG):
        return "native_cad"
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    if b"\x00" in head:
        return "other"
    lines = _Lines(_normalize(head.decode("latin-1")))
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            code = int(s)
            value = next(lines).strip().upper()
        except (ValueError, StopIteration):
            return "other"
        if code == 999:                      # a comment pair
            continue
        return "ascii_dxf" if (code == 0 and value == "SECTION") else "other"
    return "other"


# ------------------------------------------------------------ pair reader --

def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _pairs(lines: _Lines):
    """Yield ``(group code, value, line number of the value)``, strictly.

    Blank lines are skipped only where a group code is expected; the line
    right after a code is ALWAYS its value (an empty value is legal).  A
    code that is not a whole number, or a code with no value line, raises
    :class:`DxfError` naming the line.  ``lines.pos`` / ``lines.ln`` are
    current at every yield (the scan is inlined here: this loop runs once
    per line of the file)."""
    text, n, find = lines.text, lines.n, lines.text.find
    pos, ln = lines.pos, lines.ln
    while pos < n:
        j = find("\n", pos)
        if j < 0:
            j = n
        s = text[pos:j].strip()
        pos, ln = j + 1, ln + 1
        if not s:
            continue
        try:
            code = int(s)
        except ValueError:
            raise DxfError(
                f"This DXF file is damaged at line {ln}: expected a group "
                f"code (a whole number) and found {s[:24]!r}.") from None
        if pos >= n:
            raise DxfError(
                f"This DXF file is damaged at line {ln}: group code {code} "
                "has no value line after it (the file has an odd number of "
                "lines, so it was cut off or a line was lost).")
        j = find("\n", pos)
        if j < 0:
            j = n
        value = text[pos:j]
        pos, ln = j + 1, ln + 1
        lines.pos, lines.ln = pos, ln
        yield (code, value, ln)


def _num(value: str, ln: int, code: int) -> float:
    try:
        return float(value.strip())
    except ValueError:
        raise DxfError(
            f"This DXF file is damaged at line {ln}: group {code} needs a "
            f"number and found {value.strip()[:24]!r}.") from None


def _whole(value: str, ln: int, code: int) -> int:
    s = value.strip()
    try:
        return int(s)
    except ValueError:
        return int(_num(value, ln, code))


def _get(data, code, default=None):
    for c, v, _ln in data:
        if c == code:
            return v
    return default


def _f(data, code, default=None):
    for c, v, ln in data:
        if c == code:
            return _num(v, ln, code)
    return default


def _i(data, code, default=0) -> int:
    for c, v, ln in data:
        if c == code:
            return _whole(v, ln, code)
    return default


# ------------------------------------------------------------- text codes --

_PCT_CODE = re.compile(r"%%(\d{3}|.)", re.S)
_UNI_CODE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")
#: MTEXT codes that carry a parameter up to the next ';'
_MTEXT_PARAM = frozenset("fFHCcTQWApq")
#: MTEXT on/off toggles (underline, overline, strike-through)
_MTEXT_TOGGLE = frozenset("LlOoKk")


def _plain_text(s: str) -> str:
    """Single-line TEXT control codes -> plain characters: the underline /
    overline / strike toggles vanish; degree, plus-minus and diameter
    become their characters; ``\\U+XXXX`` escapes decode."""
    def pct(m):
        t = m.group(1)
        if len(t) == 3 and t.isdigit():      # %%nnn = character number nnn
            return chr(int(t)) if int(t) >= 32 else ""
        k = t.lower()
        if k in "uok":
            return ""
        if k == "d":
            return "\u00b0"
        if k == "p":
            return "\u00b1"
        if k == "c":
            return "\u00d8"
        if k == "%":
            return "%"
        return m.group(0)

    s = _PCT_CODE.sub(pct, s)
    return _UNI_CODE.sub(lambda m: chr(int(m.group(1), 16)), s)


def _mtext_plain(s: str) -> str:
    """MTEXT content -> plain text: paragraph and column breaks become a
    space, font / height / color / alignment codes and grouping braces
    drop out, a stacked fraction reads ``top/bottom``, escaped
    backslashes and braces stay.  Whitespace is collapsed."""
    out = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            c = s[i + 1]
            if c in "\\{}":
                out.append(c)
                i += 2
            elif c in "PNX~":
                out.append(" ")
                i += 2
            elif c in _MTEXT_TOGGLE:
                i += 2
            elif c == "U" and _UNI_CODE.match(s, i):
                out.append(chr(int(s[i + 3:i + 7], 16)))
                i += 7
            elif c == "S":
                j = s.find(";", i + 2)
                j = n if j < 0 else j
                out.append(re.sub(r"[\^#]", "/", s[i + 2:j]).strip())
                i = j + 1
            elif c in _MTEXT_PARAM:
                j = s.find(";", i + 2)
                i = n if j < 0 else j + 1
            else:                           # unknown escape: keep the char
                out.append(c)
                i += 2
        elif ch in "{}":
            i += 1
        elif ch == "^" and i + 1 < n and s[i + 1] in "IJM ":
            out.append("^" if s[i + 1] == " " else
                       "" if s[i + 1] == "M" else " ")
            i += 2
        else:
            out.append(ch)
            i += 1
    return " ".join(_plain_text("".join(out)).split())


# --------------------------------------------------------------- geometry --

def _cs(deg: float) -> tuple:
    """(cos, sin) of an angle in degrees, EXACT at the quadrant angles so a
    block turned 90 degrees lands on exact coordinates."""
    q = float(deg) % 360.0
    for k, cs in enumerate(((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))):
        if abs(q - 90.0 * k) <= 1e-12:
            return cs
    if abs(q - 360.0) <= 1e-12:
        return (1.0, 0.0)
    r = math.radians(float(deg))
    return (math.cos(r), math.sin(r))


def _compose(p, q) -> tuple:
    """Affine ``p`` after ``q``; an affine is ``(a, b, c, d, tx, ty)`` with
    ``x' = a*x + c*y + tx`` and ``y' = b*x + d*y + ty``."""
    pa, pb, pc, pd, ptx, pty = p
    qa, qb, qc, qd, qtx, qty = q
    return (pa * qa + pc * qb, pb * qa + pd * qb,
            pa * qc + pc * qd, pb * qc + pd * qd,
            pa * qtx + pc * qty + ptx, pb * qtx + pd * qty + pty)


def _insert_affine(ins, base, sx, sy, rot_deg) -> tuple:
    """Block coordinates -> the INSERT's space: scale, rotate, translate,
    about the block base point."""
    cs, sn = _cs(rot_deg)
    a, b, c, d = cs * sx, sn * sx, -sn * sy, cs * sy
    return (a, b, c, d,
            ins[0] - (a * base[0] + c * base[1]),
            ins[1] - (b * base[0] + d * base[1]))


def bulge_arc(p1, p2, bulge: float):
    """The arc a polyline bulge stands for between two vertices.

    Returns ``(center, radius, start_deg, end_deg)`` with the arc running
    COUNTER-CLOCKWISE from ``start_deg`` to ``end_deg`` (a negative bulge
    travels clockwise from ``p1`` to ``p2``, so its start is at ``p2``).
    ``None`` for a straight segment or coincident vertices."""
    b = float(bulge)
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    chord = math.hypot(dx, dy)
    if abs(b) < 1e-12 or chord <= 0.0:
        return None
    k = (1.0 - b * b) / (4.0 * b)            # signed center offset / chord
    cx = (p1[0] + p2[0]) / 2.0 - dy * k
    cy = (p1[1] + p2[1]) / 2.0 + dx * k
    radius = chord * (1.0 + b * b) / (4.0 * abs(b))
    a1 = math.degrees(math.atan2(p1[1] - cy, p1[0] - cx)) % 360.0
    a2 = math.degrees(math.atan2(p2[1] - cy, p2[0] - cx)) % 360.0
    return ((cx, cy), radius, a1, a2) if b > 0 else ((cx, cy), radius, a2, a1)


def _arc_box(center, radius, start_deg, end_deg) -> list:
    """Points whose bounding box is the arc's: both ends, plus every
    quadrant point the sweep passes."""
    cx, cy = center
    pts = []
    for a in (start_deg, end_deg):
        c, s = _cs(a)
        pts.append((cx + radius * c, cy + radius * s))
    sweep = (end_deg - start_deg) % 360.0
    if sweep == 0.0:
        sweep = 360.0
    for q in (0.0, 90.0, 180.0, 270.0):
        if (q - start_deg) % 360.0 <= sweep:
            c, s = _cs(q)
            pts.append((cx + radius * c, cy + radius * s))
    return pts


def _extents(entities):
    xs, ys = [], []

    def take(pts):
        for x, y in pts:
            xs.append(x)
            ys.append(y)

    for e in entities:
        take(e.pts)
        if e.kind == "CIRCLE":
            take([(e.center[0] - e.radius, e.center[1] - e.radius),
                  (e.center[0] + e.radius, e.center[1] + e.radius)])
        elif e.kind == "ARC":
            take(_arc_box(e.center, e.radius, e.start_deg, e.end_deg))
        elif e.kind == "POLYLINE" and any(e.bulges):
            n = len(e.pts)
            for k in range(n if e.closed else n - 1):
                arc = bulge_arc(e.pts[k], e.pts[(k + 1) % n], e.bulges[k])
                if arc:
                    take(_arc_box(*arc))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _apply(e: DxfEntity, xf):
    """Push an entity through an INSERT affine.  Returns the entity, or a
    skip key when the affine would turn a curve into something this model
    cannot hold (an unequal x / y scale makes a circle an ellipse)."""
    if xf is None:
        return e
    a, b, c, d, tx, ty = xf
    n1, n2 = math.hypot(a, b), math.hypot(c, d)
    uniform = (abs(n1 - n2) <= 1e-9 * max(n1, n2)
               and abs(a * c + b * d) <= 1e-9 * max(n1 * n2, 1e-300))
    mirrored = (a * d - b * c) < 0.0

    def pt(p):
        return (a * p[0] + c * p[1] + tx + 0.0, b * p[0] + d * p[1] + ty + 0.0)

    if e.kind in ("CIRCLE", "ARC"):
        if not uniform:
            return f"{e.kind}(nonuniform)"
        e.center = pt(e.center)
        e.radius = e.radius * n1
        if e.kind == "ARC":
            theta = math.degrees(math.atan2(b, a))   # where the x axis lands
            if mirrored:
                e.start_deg, e.end_deg = ((theta - e.end_deg) % 360.0,
                                          (theta - e.start_deg) % 360.0)
            else:
                e.start_deg = (e.start_deg + theta) % 360.0
                e.end_deg = (e.end_deg + theta) % 360.0
        return e
    if e.kind == "POLYLINE" and any(e.bulges):
        if not uniform:
            return "POLYLINE(nonuniform)"
        if mirrored:
            e.bulges = [-v + 0.0 for v in e.bulges]
    e.pts = [pt(p) for p in e.pts]
    if e.kind == "TEXT":
        e.height = e.height * math.sqrt(abs(a * d - b * c))
    return e


# ---------------------------------------------------------------- parsing --

def _tag(value: str) -> str:
    return value.strip().upper()


#: record types whose group pairs are kept.  Every other type is only
#: COUNTED, so its pairs (a hatch can carry thousands) are dropped as they
#: are read instead of being held in memory.
_KEPT_TYPES = frozenset({
    "LINE", "LWPOLYLINE", "POLYLINE", "VERTEX", "SEQEND", "CIRCLE", "ARC",
    "TEXT", "MTEXT", "POINT", "INSERT", "ATTRIB",
    "BLOCK", "ENDBLK", "TABLE", "ENDTAB", "LAYER"})
#: invisible / paper-space flags: kept for every type (they name the count)
_FLAG_CODES = (60, 67)


def _walk(lines: _Lines, want=(), starts: dict | None = None):
    """Yield ``(SECTION NAME, code, value, line)`` for every pair inside
    the sections named in ``want``, to the EOF marker or the end of the
    text.  Every line of the file is still checked for structure.  A
    missing ENDSEC is tolerated (a section also ends where the next one
    starts).  When ``starts`` is given, each section's body position
    ``(pos, ln)`` is recorded under its name so that body can be streamed
    again later."""
    section = None
    naming = False
    for code, value, ln in _pairs(lines):
        if code == 0:
            tag = _tag(value)
            if tag == "SECTION":
                section, naming = "", True
                continue
            if tag == "ENDSEC":
                section, naming = None, False
                continue
            if tag == "EOF":
                return
        if section is None:
            continue
        if naming:
            naming = False
            if code == 2:
                section = _tag(value)
                if starts is not None:
                    starts.setdefault(section, []).append((lines.pos, lines.ln))
                continue
        if section in want:
            yield (section, code, value, ln)


def _body(lines: _Lines):
    """The pairs of ONE section body, read from its remembered start up
    to its ENDSEC (or the next SECTION, the EOF marker, the end)."""
    for code, value, ln in _pairs(lines):
        if code == 0 and _tag(value) in ("ENDSEC", "SECTION", "EOF"):
            return
        yield (code, value, ln)


def _records(pairs):
    """Split a pair stream at every group 0: yields ``(TYPE, data, line)``
    one record at a time.  Types that are only counted keep just their
    flag pairs."""
    cur = None
    keep = False
    for code, value, ln in pairs:
        if code == 0:
            if cur is not None:
                yield cur
            typ = _tag(value)
            cur = (typ, [], ln)
            keep = typ in _KEPT_TYPES
        elif cur is not None and (keep or code in _FLAG_CODES):
            cur[1].append((code, value, ln))
    if cur is not None:
        yield cur


def _group(recs):
    """Attach VERTEX runs to their POLYLINE and attribute runs to their
    INSERT: yields ``(TYPE, data, line, children)``, streaming."""
    pending = None
    for rec in recs:
        typ = rec[0]
        if pending is not None:
            if typ == ("VERTEX" if pending[0] == "POLYLINE" else "ATTRIB"):
                pending[3].append(rec)
                continue
            parent, pending = pending, None
            yield parent
            if typ == "SEQEND" and (parent[0] == "POLYLINE" or parent[3]
                                    or _i(parent[1], 66, 0) == 1):
                continue                     # the run's own terminator
        if typ in ("POLYLINE", "INSERT"):
            pending = (typ, rec[1], rec[2], [])
        elif typ != "SEQEND":                # a bare terminator: no geometry
            yield (typ, rec[1], rec[2], None)
    if pending is not None:
        yield pending


#: planar entity types: their coordinates are stored in the entity's own
#: plane, which mirroring flips (extrusion 0,0,-1: plan x = -x)
_PLANAR_TYPES = frozenset({"CIRCLE", "ARC", "TEXT", "ATTRIB", "INSERT",
                           "LWPOLYLINE", "POLYLINE"})
_FLIP_X = (-1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _plane(typ: str, data):
    """How a record's own plane sits against the plan: ``None`` (it IS the
    plan -- the default, and always so for LINE / POINT, whose points are
    stored in plan coordinates), ``"flip"`` (extrusion 0,0,-1, what a
    mirror leaves behind: the entity's x runs the other way), or the
    ``skipped`` key for a plane this 2D reader cannot bring back."""
    if typ not in _PLANAR_TYPES and typ != "MTEXT":
        return None
    if typ == "POLYLINE" and _i(data, 70, 0) & 8:    # a 3D polyline
        return None
    nx, ny, nz = _f(data, 210, 0.0), _f(data, 220, 0.0), _f(data, 230, 1.0)
    if abs(nx) < 1e-9 and abs(ny) < 1e-9 and nz > 0.0:
        return None
    if typ == "MTEXT":
        return "MTEXT(extruded)"
    if abs(nx) < 1e-9 and abs(ny) < 1e-9 and nz < 0.0:
        return "flip"
    return f"{typ}(tilted)"


def _anchor(data, h_code: int, v_code: int):
    """Where the drafter placed a TEXT / attribute: the alignment point
    when the text is justified by a single point, else the insertion."""
    x, y = _f(data, 10), _f(data, 20)
    ax, ay = _f(data, 11), _f(data, 21)
    h, v = _i(data, h_code, 0), _i(data, v_code, 0)
    if ax is not None and ay is not None and (h in (1, 2, 4) or v != 0):
        return (ax, ay)
    if x is None or y is None:
        return None
    return (x, y)


def _text_entity(data, typ: str, h_code=72, v_code=73):
    at = _anchor(data, h_code, v_code)
    if at is None:
        return f"{typ}(incomplete)"
    text = _plain_text(_get(data, 1, "") or "").strip()
    if not text:
        return "TEXT(empty)"
    return DxfEntity("TEXT", "", [at], text=text,
                     height=_f(data, 40, 0.0))


def _convert(typ: str, data, kids):
    """One record -> a :class:`DxfEntity` in its own coordinates, or the
    ``skipped`` key it is counted under."""
    if typ == "LINE":
        v = [_f(data, c) for c in (10, 20, 11, 21)]
        if None in v:
            return "LINE(incomplete)"
        return DxfEntity("LINE", "", [(v[0], v[1]), (v[2], v[3])])
    if typ == "LWPOLYLINE":
        pts, bulges, x = [], [], None
        for code, value, ln in data:
            if code == 10:
                x = _num(value, ln, 10)
            elif code == 20 and x is not None:
                pts.append((x, _num(value, ln, 20)))
                bulges.append(0.0)
                x = None
            elif code == 42 and pts and x is None:
                bulges[-1] = _num(value, ln, 42)
        if len(pts) < 2:
            return "LWPOLYLINE(incomplete)"
        return DxfEntity("POLYLINE", "", pts, closed=bool(_i(data, 70, 0) & 1),
                         bulges=bulges)
    if typ == "POLYLINE":
        flags = _i(data, 70, 0)
        if flags & (16 | 64):
            return "POLYLINE(mesh)"
        pts, bulges = [], []
        for _t, vdata, _ln in kids or ():
            if _i(vdata, 70, 0) & 16:        # spline-frame control point
                continue
            x, y = _f(vdata, 10), _f(vdata, 20)
            if x is None or y is None:
                continue
            pts.append((x, y))
            bulges.append(_f(vdata, 42, 0.0))
        if len(pts) < 2:
            return "POLYLINE(incomplete)"
        return DxfEntity("POLYLINE", "", pts, closed=bool(flags & 1),
                         bulges=bulges)
    if typ in ("CIRCLE", "ARC"):
        x, y, r = _f(data, 10), _f(data, 20), _f(data, 40)
        if x is None or y is None or r is None or r <= 0.0:
            return f"{typ}(incomplete)"
        if typ == "CIRCLE":
            return DxfEntity("CIRCLE", "", [], center=(x, y), radius=r)
        a0, a1 = _f(data, 50), _f(data, 51)
        if a0 is None or a1 is None:
            return "ARC(incomplete)"
        return DxfEntity("ARC", "", [], center=(x, y), radius=r,
                         start_deg=a0 % 360.0, end_deg=a1 % 360.0)
    if typ == "TEXT":
        return _text_entity(data, "TEXT")
    if typ == "MTEXT":
        x, y = _f(data, 10), _f(data, 20)
        if x is None or y is None:
            return "MTEXT(incomplete)"
        chunks = [v for c, v, _ln in data if c == 3]
        text = _mtext_plain("".join(chunks) + (_get(data, 1, "") or ""))
        if not text:
            return "TEXT(empty)"
        return DxfEntity("TEXT", "", [(x, y)], text=text,
                         height=_f(data, 40, 0.0))
    if typ == "POINT":
        x, y = _f(data, 10), _f(data, 20)
        if x is None or y is None:
            return "POINT(incomplete)"
        return DxfEntity("POINT", "", [(x, y)])
    return typ                               # not read: counted by type


class _Reader:
    def __init__(self):
        self.blocks: dict = {}               # UPPER name -> {"name","base","recs"}
        self.entities: list = []
        self.skipped: dict = {}
        self.layers: dict = {}
        self._canon: dict = {}               # UPPER layer -> spelling kept

    def skip(self, key: str) -> None:
        self.skipped[key] = self.skipped.get(key, 0) + 1

    def layer(self, name: str, **table) -> str:
        """Register a layer and return the one spelling kept for it."""
        name = (name or "0").strip() or "0"
        key = name.upper()
        if key not in self._canon:
            self._canon[key] = name
            self.layers[name] = {"color": 7, "count": 0,
                                 "off": False, "frozen": False}
        canon = self._canon[key]
        if table:
            self.layers[canon].update(table)
        return canon

    def emit(self, rec, xf, inherit, path) -> None:
        typ, data, _ln, kids = rec
        if _i(data, 67, 0) == 1:
            return self.skip("PAPERSPACE")
        if _i(data, 60, 0) == 1:
            return self.skip("INVISIBLE")
        own = (_get(data, 8, "0") or "0").strip() or "0"
        layer = inherit if (own == "0" and inherit is not None) else own
        plane = _plane(typ, data)
        if plane not in (None, "flip"):
            return self.skip(plane)
        mine = xf
        if plane == "flip":
            mine = _FLIP_X if xf is None else _compose(xf, _FLIP_X)
        if typ == "INSERT":
            return self.expand(data, kids, xf, mine, layer, inherit, path)
        res = _convert(typ, data, kids)
        self.place(res, layer, mine, path)

    def place(self, res, layer, xf, path) -> None:
        if isinstance(res, str):
            return self.skip(res)
        res = _apply(res, xf)
        if isinstance(res, str):
            return self.skip(res)
        res.layer = self.layer(layer)
        res.block = "/".join(path)
        self.layers[res.layer]["count"] += 1
        self.entities.append(res)

    def expand(self, data, attribs, xf, mine, layer, inherit, path) -> None:
        """``xf`` maps the space the INSERT sits in to the plan; ``mine``
        is the same with the INSERT's own plane applied (block content)."""
        name = (_get(data, 2, "") or "").strip()
        block = self.blocks.get(name.upper())
        if block is not None:
            name = block["name"]             # one spelling per block
        # attribute values are stored in the space the INSERT sits in,
        # each on its own plane
        for _t, adata, _ln in attribs or ():
            if _i(adata, 67, 0) == 1:
                self.skip("PAPERSPACE")
                continue
            if _i(adata, 70, 0) & 1 or _i(adata, 60, 0) == 1:
                self.skip("ATTRIB(invisible)")
                continue
            plane = _plane("ATTRIB", adata)
            if plane not in (None, "flip"):
                self.skip(plane)
                continue
            axf = xf
            if plane == "flip":
                axf = _FLIP_X if xf is None else _compose(xf, _FLIP_X)
            own = (_get(adata, 8, "0") or "0").strip() or "0"
            alayer = inherit if (own == "0" and inherit is not None) else own
            self.place(_text_entity(adata, "ATTRIB", 72, 74), alayer, axf,
                       path + [name])
        ix, iy = _f(data, 10), _f(data, 20)
        sx, sy = _f(data, 41, 1.0), _f(data, 42, 1.0)
        if (block is None or ix is None or iy is None
                or len(path) >= MAX_INSERT_DEPTH
                or name.upper() in (p.upper() for p in path)
                or abs(sx) < 1e-12 or abs(sy) < 1e-12):
            return self.skip("INSERT(unresolved)")
        if _i(data, 70, 1) > 1 or _i(data, 71, 1) > 1:
            return self.skip("INSERT(array)")
        local = _insert_affine((ix, iy), block["base"], sx, sy,
                               _f(data, 50, 0.0))
        total = local if mine is None else _compose(mine, local)
        for rec in block["recs"]:
            self.emit(rec, total, layer, path + [name])


def read(path: str) -> DxfDoc:
    """Read an ASCII DXF background.  :class:`DxfError` for anything else
    (:data:`NOT_ASCII_DXF`) and for a structurally broken file (the line
    is named)."""
    if sniff(path) != "ascii_dxf":
        raise DxfError(NOT_ASCII_DXF)
    with open(path, "rb") as f:
        text = _normalize(_decode(f.read()))
    rd = _Reader()

    # pass 1, the whole file: the declared units (HEADER), and the pairs
    # of the two sections that are kept (TABLES, BLOCKS).  ENTITIES is
    # only located here -- it is streamed in pass 2, once every block
    # definition is known.
    insunits = None
    var = None
    tables: list = []
    blocks: list = []
    keep = False
    starts: dict = {}
    for sec, code, value, ln in _walk(_Lines(text),
                                      ("HEADER", "TABLES", "BLOCKS"), starts):
        if sec == "HEADER":
            if code == 9:
                var = _tag(value)
            elif var == "$INSUNITS" and code == 70:
                insunits = _whole(value, ln, 70)
        elif sec in ("TABLES", "BLOCKS"):
            if code == 0:
                keep = _tag(value) in _KEPT_TYPES
            if code == 0 or keep or code in _FLAG_CODES:
                (tables if sec == "TABLES" else blocks).append((code, value, ln))

    # the LAYER table (a negative color means the layer is off)
    table = None
    for typ, data, _ln in _records(tables):
        if typ == "TABLE":
            table = _tag(_get(data, 2, "") or "")
        elif typ == "ENDTAB":
            table = None
        elif typ == "LAYER" and table == "LAYER":
            name = (_get(data, 2, "") or "").strip()
            if name:
                color = _i(data, 62, 7)
                rd.layer(name, color=abs(color), off=color < 0,
                         frozen=bool(_i(data, 70, 0) & 1))

    # block definitions, expanded only where an INSERT places them
    cur = None
    for typ, data, ln in _records(blocks):
        if typ == "BLOCK":
            name = (_get(data, 2, None) or _get(data, 3, "") or "").strip()
            cur = {"name": name, "recs": [],
                   "base": (_f(data, 10, 0.0), _f(data, 20, 0.0))}
            if name:
                rd.blocks[name.upper()] = cur
        elif typ == "ENDBLK":
            cur = None
        elif cur is not None:
            cur["recs"].append((typ, data, ln))
    for block in rd.blocks.values():
        block["recs"] = list(_group(block["recs"]))

    # pass 2: ENTITIES, one record at a time
    for pos, ln in starts.get("ENTITIES", ()):
        for rec in _group(_records(_body(_Lines(text, pos, ln)))):
            rd.emit(rec, None, None, [])

    return DxfDoc(path=path, units=INSUNITS.get(insunits, ""),
                  insunits=insunits, layers=rd.layers, entities=rd.entities,
                  skipped=rd.skipped, extents=_extents(rd.entities))


# ------------------------------------------------------------ layer roles --

#: role -> the layer-name tokens that claim it.  ``CONC`` and ``EDGE`` are
#: deliberately absent: alone they say nothing (S-WALL-CONC is a wall
#: because of WALL, S-SLAB-EDGE a slab because of SLAB).
ROLE_TOKENS = {
    "grid": frozenset({"GRID", "GRIDS", "GRD", "GRIDLINE", "GRIDLINES"}),
    "wall": frozenset({"WALL", "WALLS", "SHEARWALL", "SHEARWALLS", "SW"}),
    "column": frozenset({"COL", "COLS", "COLUMN", "COLUMNS"}),
    "masonry": frozenset({"CMU", "MASONRY", "MSNRY", "BLOCK"}),
    "slab": frozenset({"SLAB", "SLABS", "SLABEDGE"}),
    "opening": frozenset({"OPEN", "OPNG", "OPNGS", "OPENING", "OPENINGS",
                          "PEN", "PENETRATION", "PENETRATIONS"}),
}
#: (winner, loser): the ONE precedence rule -- the more specific token wins
#: over the thing it qualifies.  A masonry wall layer is masonry; an
#: opening in a slab, wall or masonry wall is an opening.  Every other
#: double match is "unknown".
_OUTRANKS = frozenset({("masonry", "wall"), ("opening", "slab"),
                       ("opening", "wall"), ("opening", "masonry")})
_TITLE_TOKENS = frozenset({"TITLE", "TTL", "TTLB"})
_BOUND_REF = re.compile(r"\$\d+\$")


def layer_role(name: str) -> str:
    """The role one layer NAME claims, or ``"unknown"``.

    The name is uppercased and split on ``-``, ``_``, ``$`` and spaces; a
    reference prefix (``FILE|LAYER``, or ``FILE$0$LAYER`` once bound) is
    cut first so only the layer's own name speaks.  Exactly one matching
    role wins.  Two matching roles are "unknown" unless one outranks the
    other under the single precedence rule (the more specific token wins:
    masonry over wall; opening over slab, wall and masonry).  ``BLOCK``
    beside a title token (a title block layer) is not masonry.  Nothing is
    ever inferred from geometry."""
    own = str(name).upper().rsplit("|", 1)[-1]
    own = _BOUND_REF.split(own)[-1]
    tokens = {t for t in re.split(r"[-_$\s]+", own) if t}
    if tokens & _TITLE_TOKENS:
        tokens.discard("BLOCK")
    hits = [role for role, words in ROLE_TOKENS.items() if tokens & words]
    if len(hits) == 1:
        return hits[0]
    for role in hits:
        if all((role, other) in _OUTRANKS for other in hits if other != role):
            return role
    return "unknown"


def classify_layers(doc: DxfDoc) -> dict:
    """Role -> sorted layer names, for every layer in the file: ``grid``,
    ``wall``, ``column``, ``masonry``, ``slab``, ``opening`` and
    ``unknown`` (every other layer -- listed, never guessed).  See
    :func:`layer_role` for the rules."""
    out = {role: [] for role in ROLE_TOKENS}
    out["unknown"] = []
    for name in sorted(doc.layers):
        out[layer_role(name)].append(name)
    return out


# ----------------------------------------------------------------- writer --

_TEXT_H = 1.0             # default text height, drawing units
_SHAPE_KINDS = ("line", "poly", "circle", "arc", "text", "point")


def _clean(v) -> str:
    """A control character inside a value would be read as the next group
    code; collapse each to a space (the Fieldstitch exporter's rule)."""
    return "".join(" " if ord(ch) < 0x20 else ch for ch in str(v))


def _xy(shape, key, what):
    try:
        x, y = shape[key]
        return float(x), float(y)
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"background shape {what} needs {key!r} as an "
                         "(x, y) pair") from None


def write_background(path: str, shapes, *, units: str = "ft",
                     extra_header=None, precision: int = 4) -> int:
    """Write an ASCII DXF R12 background; returns the entity count.

    ``shapes`` is a list of dicts::

        {"layer": str, "kind": "line", "pts": [(x, y), (x, y)]}
        {"layer": str, "kind": "poly", "pts": [...], "closed": bool}
        {"layer": str, "kind": "circle", "center": (x, y), "radius": r}
        {"layer": str, "kind": "arc", "center": (x, y), "radius": r,
         "start_deg": a, "end_deg": b}
        {"layer": str, "kind": "text", "at": (x, y), "text": str, "height": h}
        {"layer": str, "kind": "point", "at": (x, y), "text": optional label}

    A ``poly`` may carry ``"bulges"`` (one per vertex) for arc segments.
    Polylines are written in the R12 form (POLYLINE / VERTEX / SEQEND,
    flag 1 when closed); a labeled point is a POINT plus a TEXT.  The
    HEADER carries ``$ACADVER`` AC1009 and ``$INSUNITS`` for ``units``
    (``""`` writes none: an undeclared-units file); ``extra_header`` is an
    iterable of ``(group code, value)`` pairs appended to the HEADER.

    Every layer name must ALREADY satisfy ``selvage.validate_dxf_layer``
    (``ValueError`` otherwise, and nothing is written): the layer-name
    rule is enforced at creation, never silently fixed -- a caller holding
    a free-form name runs it through ``selvage.dxf_layer_name`` first, on
    purpose.  The count
    returned equals ``len(read(path).entities)``.  Write is atomic."""
    units = str(units)
    if units and units not in _INSUNITS_CODE:
        raise ValueError(f"unknown units {units!r}; expected one of "
                         f"{sorted(_INSUNITS_CODE)} or '' for undeclared")
    shapes = list(shapes)
    layers: list = []
    for k, sh in enumerate(shapes):
        kind = sh.get("kind")
        if kind not in _SHAPE_KINDS:
            raise ValueError(f"background shape {k} has unknown kind "
                             f"{kind!r}; expected one of {_SHAPE_KINDS}")
        layer = str(sh.get("layer", ""))
        validate_dxf_layer(layer)
        if layer not in layers:
            layers.append(layer)

    def n(v) -> str:
        s = f"{float(v):.{int(precision)}f}"
        return s[1:] if s.startswith("-") and float(s) == 0.0 else s

    pairs: list = [(0, "SECTION"), (2, "HEADER"),
                   (9, "$ACADVER"), (1, "AC1009")]
    if units:
        pairs += [(9, "$INSUNITS"), (70, str(_INSUNITS_CODE[units]))]
    for code, value in extra_header or ():
        pairs.append((int(code), _clean(value)))
    pairs += [(0, "ENDSEC"),
              (0, "SECTION"), (2, "TABLES"),
              (0, "TABLE"), (2, "LAYER"), (70, str(len(layers)))]
    for layer in layers:
        pairs += [(0, "LAYER"), (2, layer), (70, "0"), (62, "7"),
                  (6, "CONTINUOUS")]
    pairs += [(0, "ENDTAB"), (0, "ENDSEC"),
              (0, "SECTION"), (2, "ENTITIES")]

    count = 0
    for k, sh in enumerate(shapes):
        kind, layer = sh["kind"], str(sh["layer"])
        what = f"{k} ({kind})"
        if kind in ("line", "poly"):
            try:
                pts = [(float(x), float(y)) for x, y in sh["pts"]]
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"background shape {what} needs 'pts' as "
                                 "(x, y) pairs") from None
            if kind == "line":
                if len(pts) != 2:
                    raise ValueError(f"background shape {what} needs "
                                     "exactly two points")
                pairs += [(0, "LINE"), (8, layer),
                          (10, n(pts[0][0])), (20, n(pts[0][1])), (30, n(0)),
                          (11, n(pts[1][0])), (21, n(pts[1][1])), (31, n(0))]
            else:
                if len(pts) < 2:
                    raise ValueError(f"background shape {what} needs at "
                                     "least two points")
                bulges = [float(b) for b in sh.get("bulges") or ()]
                if bulges and len(bulges) != len(pts):
                    raise ValueError(f"background shape {what}: 'bulges' "
                                     "needs one value per vertex")
                pairs += [(0, "POLYLINE"), (8, layer), (66, "1"),
                          (70, "1" if sh.get("closed") else "0")]
                for j, (x, y) in enumerate(pts):
                    pairs += [(0, "VERTEX"), (8, layer),
                              (10, n(x)), (20, n(y)), (30, n(0))]
                    if bulges and bulges[j]:
                        pairs.append((42, f"{bulges[j]:.10g}"))
                pairs += [(0, "SEQEND"), (8, layer)]
        elif kind in ("circle", "arc"):
            cx, cy = _xy(sh, "center", what)
            try:
                r = float(sh["radius"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"background shape {what} needs a "
                                 "'radius'") from None
            if r <= 0.0:
                raise ValueError(f"background shape {what} needs a "
                                 "positive radius")
            pairs += [(0, kind.upper()), (8, layer),
                      (10, n(cx)), (20, n(cy)), (30, n(0)), (40, n(r))]
            if kind == "arc":
                try:
                    a0, a1 = float(sh["start_deg"]), float(sh["end_deg"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError(f"background shape {what} needs "
                                     "'start_deg' and 'end_deg'") from None
                pairs += [(50, n(a0)), (51, n(a1))]
        else:                                # text | point
            x, y = _xy(sh, "at", what)
            h = float(sh.get("height") or _TEXT_H)
            label = _clean(sh.get("text") or "")
            if kind == "text":
                if not label.strip():
                    raise ValueError(f"background shape {what} needs a "
                                     "non-empty 'text'")
            else:
                pairs += [(0, "POINT"), (8, layer),
                          (10, n(x)), (20, n(y)), (30, n(0))]
                count += 1
                if not label.strip():
                    continue
                x, y = x + h * 0.5, y + h * 0.5
            pairs += [(0, "TEXT"), (8, layer),
                      (10, n(x)), (20, n(y)), (30, n(0)), (40, n(h)),
                      (1, label)]
        count += 1
    pairs += [(0, "ENDSEC"), (0, "EOF")]
    text = "".join(f"{code}\r\n{value}\r\n" for code, value in pairs)
    atomic_write_bytes(text.encode("ascii", errors="replace"), path)
    return count
