"""The Loft: Planloom's original 2D construction-drafting engine (GUI-free).

Everything on a mold-loft floor gets drawn full size before it gets built;
this module is that floor for Planloom.  The naming grammar (registry in
HANDOFF.md):

* **Loft** — the drawing mode itself; drawings persist as ``*.loft.json``.
* **Plies** — layers, stacked like plywood veneers (:class:`Ply`), named
  with a hyphenated trade-element grammar of our own ("A-WALL", "P-FIXT").
* **Plumbline** — the precision system: :func:`snap` finds endpoints,
  midpoints, intersections, perpendicular feet and grid lines, with ortho
  and 45° polar projection as the fallback.
* **Stencils** — the symbol library (:data:`STENCILS`), named for the green
  plastic drafting templates fixtures used to be traced from.
* **Plates** — exported sheets with a title block (:func:`plate_pdf`);
  drawings in the old handbooks are "plates".
* Binder / Traits / Shorthand / Tally are GUI-side ideas built on this
  engine (entity tree, properties panel, one-key tools, live quantities).

Coordinate conventions: model space is **decimal feet, y UP (north up)** —
east = +x, north = +y, exactly the Fieldstitch world frame, so layout points
and the extruded wireframe land in the same world with no transform.  The
GUI canvas flips y for the screen; reportlab's y-up paper space needs no
flip.  Paper-relative sizes (text heights, grid bubbles, dimension ticks)
are stored in paper inches and convert to model feet through the sheet
scale: ``model_ft = paper_in * ratio / 12``.  Angles are degrees CCW from
+x; arcs sweep CCW from a0 to a1 (mod 360).

Fully offline: stdlib + reportlab only; the PDF rasterizer is imported
lazily by :func:`to_png` and stays optional.  All writes are atomic
(temp file + fsync + ``os.replace``).
"""
from __future__ import annotations

import copy
import io
import json
import math
import os
import re
import string
from dataclasses import dataclass, field
from datetime import date

# ------------------------------------------------------------- constants ---

#: Architectural scale ladder, label -> ratio (1/8" = 1'-0" means 1 paper
#: inch represents 96 real inches, hence ratio 96).
SCALES: list[tuple[str, int]] = [
    ("1/16\" = 1'-0\"", 192),
    ("3/32\" = 1'-0\"", 128),
    ("1/8\" = 1'-0\"", 96),
    ("3/16\" = 1'-0\"", 64),
    ("1/4\" = 1'-0\"", 48),
    ("3/8\" = 1'-0\"", 32),
    ("1/2\" = 1'-0\"", 24),
    ("3/4\" = 1'-0\"", 16),
    ("1\" = 1'-0\"", 12),
    ("1 1/2\" = 1'-0\"", 8),
    ("3\" = 1'-0\"", 4),
]

#: Lettering heights in paper inches — the classic hand-lettering ladder:
#: 3/32" notes, 1/8" subtitles, 3/16" titles.
TEXT_SIZES: dict[str, float] = {
    "body": 3.0 / 32.0,
    "sub": 1.0 / 8.0,
    "title": 3.0 / 16.0,
}

#: Wall assemblies with real plan thicknesses.  Stud partitions are actual
#: framing (3-1/2" / 5-1/2") plus one layer of 5/8" board each side; masonry
#: units are actual size (nominal minus the 3/8" joint); cast walls are cast
#: at the called dimension.  Labels stay generic — assemblies, not brands.
WALL_TYPES: dict[str, dict] = {
    "stud4":  {"label": 'Stud partition 4-3/4"', "thick_in": 4.75},
    "stud6":  {"label": 'Stud partition 6-3/4"', "thick_in": 6.75},
    "furr":   {"label": 'Furring 1-1/2"', "thick_in": 1.5},
    "cmu8":   {"label": 'CMU 8"', "thick_in": 7.625},
    "cmu12":  {"label": 'CMU 12"', "thick_in": 11.625},
    "conc8":  {"label": 'Cast concrete 8"', "thick_in": 8.0},
    "conc12": {"label": 'Cast concrete 12"', "thick_in": 12.0},
}

#: Pen-weight ladder in paper millimetres (the technical-pen tip sizes).
#: Walls plot "cut", fixtures/doors "medium", grids/dims/annotation
#: "light"/"fine".
WEIGHTS: dict[str, float] = {
    "fine": 0.18,
    "light": 0.25,
    "medium": 0.35,
    "heavy": 0.50,
    "cut": 0.70,
}

#: Dash patterns in paper inches (on/off pairs, empty = solid).
LINETYPES: dict[str, tuple] = {
    "solid": (),
    "hidden": (0.125, 0.0625),
    "center": (0.75, 0.0625, 0.125, 0.0625),
    "phantom": (1.0, 0.0625, 0.125, 0.0625, 0.125, 0.0625),
}

#: Sheet sizes in paper inches, landscape (w, h).
SHEET_SIZES: dict[str, tuple] = {
    "ANSI B": (17, 11),
    "ANSI D": (34, 22),
    "ARCH C": (24, 18),
    "ARCH D": (36, 24),
    "ARCH E1": (42, 30),
}

#: Letters skipped in grid lettering — I and O read as 1 and 0 on a print.
GRID_SKIP = {"I", "O"}

#: Paper sizes for annotation graphics (inches).
GRID_BUBBLE_IN = 0.4375        # grid bubble diameter (~7/16")
CALLOUT_DIA_IN = 0.625         # detail-callout bubble diameter
DIM_GAP_IN = 0.0625            # extension line stands 1/16" clear of the work
DIM_OVERSHOOT_IN = 0.125       # extension line runs 1/8" past the dim line
DIM_TICK_IN = 0.125            # 45° architectural tick length
PIPE_SYM_IN = 0.09             # pipe fitting-symbol radius (Pipewright)

UNDO_LIMIT = 1000


def text_model_h(size_key: str, ratio: int) -> float:
    """Paper lettering height -> model feet at the given scale ratio."""
    return TEXT_SIZES.get(str(size_key), TEXT_SIZES["body"]) * ratio / 12.0


def weight_pt(name: str) -> float:
    """Pen weight in paper mm -> PDF points (mm / 25.4 * 72)."""
    return WEIGHTS.get(str(name), WEIGHTS["light"]) / 25.4 * 72.0


def _scale_label(ratio: int) -> str:
    for label, r in SCALES:
        if r == int(ratio):
            return label
    return f"1:{int(ratio)}"


# ----------------------------------------------------------- atomic write ---

def _atomic_bytes(data: bytes, out_path: str) -> None:
    """Write beside out_path, fsync, then atomically replace: a killed
    process can never leave a truncated file at the final path."""
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)


# ------------------------------------------------------------- DXF colors ---

#: Hex anchors -> classic DXF color-index integers (group code 62); black
#: and white share index 7 by convention.  Local copy of the Fieldstitch
#: palette so importing the Loft never drags in the PDF toolchain.
ACI_COLORS = {
    "#ff0000": 1,
    "#ffff00": 2,
    "#00ff00": 3,
    "#00ffff": 4,
    "#0000ff": 5,
    "#ff00ff": 6,
    "#ffffff": 7,
    "#000000": 7,
    "#808080": 8,
    "#ff7f00": 30,
}


def _rgb(hex_color: str) -> tuple[int, int, int]:
    s = str(hex_color or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"bad hex color {hex_color!r} (use #rrggbb)")
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        raise ValueError(f"bad hex color {hex_color!r} (use #rrggbb)") from None


_ACI_ANCHORS = [(_rgb(h), n) for h, n in ACI_COLORS.items()]


def aci_for(hex_color: str) -> int:
    """Nearest DXF color index for a hex color, by RGB distance."""
    r, g, b = _rgb(hex_color)
    best, best_d = 7, None
    for (ar, ag, ab), n in _ACI_ANCHORS:
        d = (r - ar) ** 2 + (g - ag) ** 2 + (b - ab) ** 2
        if best_d is None or d < best_d:
            best, best_d = n, d
    return best


# -------------------------------------------------------------- data model --

@dataclass
class Ply:
    """One drawing layer.  Weight/linetype are style keys into WEIGHTS /
    LINETYPES; halftone and locked are honored by the GUI, not the engine."""
    name: str
    color: str = "#888888"
    weight: str = "light"
    linetype: str = "solid"
    visible: bool = True
    locked: bool = False
    halftone: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "color": self.color,
                "weight": self.weight, "linetype": self.linetype,
                "visible": self.visible, "locked": self.locked,
                "halftone": self.halftone}

    @classmethod
    def from_dict(cls, d: dict) -> "Ply":
        return cls(name=str(d.get("name", "PLY")),
                   color=str(d.get("color", "#888888")),
                   weight=str(d.get("weight", "light")),
                   linetype=str(d.get("linetype", "solid")),
                   visible=bool(d.get("visible", True)),
                   locked=bool(d.get("locked", False)),
                   halftone=bool(d.get("halftone", False)))


@dataclass
class Ent:
    """One drawn entity.  ``pts`` are model-feet (x, y) tuples whose meaning
    depends on ``kind``; doors and windows carry NO pts at all — their whole
    geometry derives from the host wall and the ``t`` parameter, so they
    ride along automatically when the host moves."""
    id: str
    kind: str          # wall|door|window|fixture|line|grid|room|text|dim|
                       # callout|pipe
    ply: str
    pts: list = field(default_factory=list)
    props: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "ply": self.ply,
                "pts": [[float(x), float(y)] for (x, y) in self.pts],
                "props": dict(self.props)}

    @classmethod
    def from_dict(cls, d: dict) -> "Ent":
        return cls(id=str(d.get("id", "")),
                   kind=str(d.get("kind", "line")),
                   ply=str(d.get("ply", "G-ANNO")),
                   pts=[(float(p[0]), float(p[1])) for p in d.get("pts") or []],
                   props=dict(d.get("props") or {}))


#: The stock arch + plumbing floor-plan ply set (trade-element grammar).
DEFAULT_PLIES: list[Ply] = [
    Ply("A-WALL", color="#c0392b", weight="cut"),
    Ply("A-DOOR", color="#27ae60", weight="medium"),
    Ply("A-GLAZ", color="#2980b9", weight="medium"),
    Ply("P-FIXT", color="#8e44ad", weight="medium"),
    # Pipewright system plies — colors mirror pipewright.SYSTEMS; vents are
    # dashed in plan (hidden linetype), gas draws phantom by convention.
    Ply("P-SAN", color="#1e8449", weight="heavy"),
    Ply("P-VENT", color="#52be80", weight="light", linetype="hidden"),
    Ply("P-STRM", color="#7d6608", weight="heavy"),
    Ply("P-DCW", color="#2471a3", weight="medium"),
    Ply("P-DHW", color="#cb4335", weight="medium"),
    Ply("P-GAS", color="#d4ac0d", weight="medium", linetype="phantom"),
    Ply("Q-EQPT", color="#d35400", weight="medium"),
    Ply("S-GRID", color="#16a085", weight="light", linetype="center"),
    Ply("G-DIMS", color="#b7950b", weight="fine"),
    Ply("G-ANNO", color="#c2185b", weight="light"),
]

_DEFAULT_PLY = {
    "wall": "A-WALL", "door": "A-DOOR", "window": "A-GLAZ",
    "fixture": "P-FIXT", "grid": "S-GRID", "dim": "G-DIMS",
    "room": "G-ANNO", "text": "G-ANNO", "callout": "G-ANNO",
    "line": "G-ANNO",
}


# ------------------------------------------------------- feet-inches text ---

def fmt_ftin(feet: float, denom: int = 16) -> str:
    """Decimal feet -> ``12'-4 1/2"`` (nearest 1/denom inch, fraction
    reduced, ``0'-6"`` for sub-foot values, negative safe)."""
    denom = max(1, int(denom))
    units = round(abs(float(feet)) * 12.0 * denom)   # whole 1/denom inches
    sign = "-" if (feet < 0 and units) else ""
    whole_in, frac = divmod(units, denom)
    ft, inch = divmod(whole_in, 12)
    if frac:
        g = math.gcd(frac, denom)
        return f"{sign}{ft}'-{inch} {frac // g}/{denom // g}\""
    return f"{sign}{ft}'-{inch}\""


_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "′": "'",       # curly / prime feet
    "“": '"', "”": '"', "″": '"',       # curly / prime inches
})
_FRACTION = re.compile(r"^(\d+)\s*/\s*(\d+)$")
_MIXED = re.compile(r"^(\d+(?:\.\d+)?)(?:\s+(\d+)\s*/\s*(\d+))?$")


def _num(text: str) -> float | None:
    """``4`` / ``4.5`` / ``4 1/2`` / ``1/2`` -> float, else None."""
    s = " ".join(str(text).split())
    m = _FRACTION.match(s)
    if m:
        d = int(m.group(2))
        return int(m.group(1)) / d if d else None
    m = _MIXED.match(s)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2):
        d = int(m.group(3))
        if not d:
            return None
        v += int(m.group(2)) / d
    return v


def parse_ftin(text) -> float | None:
    """Tolerant feet-inches reader -> decimal feet (None when unreadable).

    Accepts ``12'-6"``, ``12' 6``, ``3'6``, ``12.5'``, ``150"``,
    ``12'-4 1/2"``, ``4 1/2"`` and bare numbers (bare number = feet).
    Unicode quotes/primes and stray whitespace are tolerated."""
    if text is None:
        return None
    s = str(text).translate(_QUOTE_MAP).strip()
    if not s:
        return None
    neg = s.startswith("-")
    if neg:
        s = s[1:].strip()
    if "'" in s:
        head, _, rest = s.partition("'")
        try:
            feet = float(head.strip())
        except ValueError:
            return None
        rest = rest.strip().lstrip("-").strip().rstrip('"').strip()
        inches = _num(rest) if rest else 0.0
        if inches is None:
            return None
    elif s.endswith('"'):
        feet = 0.0
        inches = _num(s[:-1].strip())
        if inches is None:
            return None
    else:
        feet = _num(s)
        if feet is None:
            return None
        inches = 0.0
    total = feet + inches / 12.0
    return -total if neg else total


# ---------------------------------------------------------- geometry core ---

_EPS = 1e-9


def _seg_x(a1, a2, b1, b2):
    """Proper segment-segment intersection point, or None.  Parallel and
    collinear pairs return None on purpose: an overlap has no single
    crossing worth snapping to."""
    rx, ry = a2[0] - a1[0], a2[1] - a1[1]
    sx, sy = b2[0] - b1[0], b2[1] - b1[1]
    den = rx * sy - ry * sx
    scale = math.hypot(rx, ry) * math.hypot(sx, sy)
    if abs(den) <= 1e-12 + 1e-9 * scale:
        return None
    qx, qy = b1[0] - a1[0], b1[1] - a1[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return (a1[0] + rx * t, a1[1] + ry * t)
    return None


def _line_x(p, d, q, e):
    """Infinite-line intersection (point + direction each), None if parallel."""
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) <= 1e-12:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return (p[0] + d[0] * t, p[1] + d[1] * t)


def _closest_on_seg(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= _EPS:
        return (a[0], a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    t = min(1.0, max(0.0, t))
    return (a[0] + dx * t, a[1] + dy * t)


def _perp_foot(a, b, p):
    """Perpendicular foot of p on segment a-b, None when it falls outside."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= _EPS:
        return None
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    if not (-1e-9 <= t <= 1.0 + 1e-9):
        return None
    return (a[0] + dx * t, a[1] + dy * t)


def _solve_t(a, b, p) -> float:
    """Projection parameter of p along a->b, clamped to 0.05..0.95 so a
    dragged opening can never fall off the end of its host wall."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= _EPS:
        return 0.5
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    return min(0.95, max(0.05, t))


def offset_pair(a, b, half_w):
    """The two wall-face segments at +/- half_w off the centerline a->b.
    First face is on the LEFT of travel (the +n side)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= _EPS:
        return ((tuple(a), tuple(b)), (tuple(a), tuple(b)))
    nx, ny = -dy / length * half_w, dx / length * half_w
    return (((a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny)),
            ((a[0] - nx, a[1] - ny), (b[0] - nx, b[1] - ny)))


def _wall_frame(ent):
    """(a, b, L, u, n, half_ft) of a wall, or None when degenerate.
    u = unit direction a->b, n = left normal, half = half thickness."""
    if len(ent.pts) != 2:
        return None
    a, b = ent.pts
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length <= _EPS:
        return None
    u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    n = (-u[1], u[0])
    half = float(ent.props.get("thick_in", 4.75)) / 24.0
    return a, b, length, u, n, half


def _lerp(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


# ------------------------------------------------------------ grid labels ---

_ALPHA = [ch for ch in string.ascii_uppercase if ch not in GRID_SKIP]


def _alpha_label(n: int) -> str:
    """1 -> A ... 24 -> Z, 25 -> AA (bijective over the 24-letter grid
    alphabet — I and O never appear)."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, len(_ALPHA))
        out = _ALPHA[r] + out
    return out


def _alpha_index(label: str) -> int | None:
    s = str(label).strip().upper()
    if not s:
        return None
    n = 0
    for ch in s:
        if ch not in _ALPHA:
            return None
        n = n * len(_ALPHA) + (_ALPHA.index(ch) + 1)
    return n


# ------------------------------------------------------------ draft model ---

class DraftModel:
    """One Loft drawing: sheet identity + plies + entities.

    All mutations go through :meth:`add` / :meth:`remove` / :meth:`update` /
    :meth:`move` and the ply methods, so every change lands on the undo
    stack.  Undo is snapshot-based and rebuilds entities from plain dicts —
    snapshots never alias live objects, which also means Ent handles held
    across an undo()/redo() are stale; re-fetch with :meth:`entity`.
    """

    def __init__(self, title: str = "", number: str = "",
                 scale_ratio: int = 96):
        self.title = str(title)
        self.number = str(number)
        self.scale_ratio = int(scale_ratio)
        self.plies: list[Ply] = [Ply.from_dict(p.to_dict())
                                 for p in DEFAULT_PLIES]
        self.ents: list[Ent] = []
        self.path: str | None = None
        self.dirty = False
        self._next_id = 1
        self._undo: list[dict] = []
        self._redo: list[dict] = []

    # ------------------------------------------------------------- undo --

    def _snapshot(self) -> dict:
        return {"title": self.title, "number": self.number,
                "scale_ratio": self.scale_ratio,
                "plies": [p.to_dict() for p in self.plies],
                "ents": [copy.deepcopy(e.to_dict()) for e in self.ents],
                "next_id": self._next_id}

    def _restore(self, snap: dict) -> None:
        self.title = snap["title"]
        self.number = snap["number"]
        self.scale_ratio = snap["scale_ratio"]
        self.plies = [Ply.from_dict(d) for d in snap["plies"]]
        self.ents = [Ent.from_dict(d) for d in snap["ents"]]
        self._next_id = snap["next_id"]

    def _commit(self, snap: dict) -> None:
        self._undo.append(snap)
        if len(self._undo) > UNDO_LIMIT:
            del self._undo[0]
        self._redo.clear()
        self.dirty = True

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        if len(self._undo) > UNDO_LIMIT:
            del self._undo[0]
        self._restore(self._redo.pop())
        self.dirty = True
        return True

    # ---------------------------------------------------------- entities --

    def add(self, kind, pts, ply=None, **props) -> Ent:
        """Add an entity; per-kind defaults are filled in.  Doors/windows
        are parametric: a clicked point is converted to the ``t`` parameter
        along the host wall and ``pts`` is emptied — their geometry always
        derives live from the host (see :func:`door_geometry`)."""
        snap_state = self._snapshot()
        kind = str(kind)
        pts = [(float(x), float(y)) for (x, y) in (pts or [])]
        props = dict(props)
        if ply is None:
            if kind == "pipe":     # ply follows the system (P-SAN, P-DCW...)
                from .pipewright import SYSTEMS as _PIPE_SYSTEMS
                spec = (_PIPE_SYSTEMS.get(str(props.get("system", "san")))
                        or _PIPE_SYSTEMS["san"])
                ply = spec["ply"]
            else:
                ply = _DEFAULT_PLY.get(kind, "G-ANNO")
        if kind == "wall":
            wtype = str(props.setdefault("wtype", "stud4"))
            if "thick_in" not in props:
                props["thick_in"] = float(
                    WALL_TYPES.get(wtype, WALL_TYPES["stud4"])["thick_in"])
            props["thick_in"] = float(props["thick_in"])
        elif kind in ("door", "window"):
            props.setdefault("width_in", 36.0 if kind == "door" else 48.0)
            props["width_in"] = float(props["width_in"])
            if kind == "door":
                props.setdefault("swing", "in")
                props.setdefault("hand", "l")
            host = self.entity(props.get("host"))
            if "t" in props:
                props["t"] = min(1.0, max(0.0, float(props["t"])))
            elif host is not None and len(host.pts) == 2 and pts:
                props["t"] = _solve_t(host.pts[0], host.pts[1], pts[0])
            else:
                props["t"] = 0.5
            pts = []            # parametric on the host, never stored
        elif kind == "fixture":
            props.setdefault("rot", 0.0)
            props.setdefault("flip", False)
        elif kind == "pipe":
            # a run: pts is the flow polyline (first -> last vertex);
            # invert_ft = invert elevation at the FIRST vertex, positive
            # slope_in_ft (in/ft) falls toward the LAST vertex.
            from .pipewright import SYSTEMS as _PIPE_SYSTEMS
            spec = (_PIPE_SYSTEMS.get(str(props.setdefault("system", "san")))
                    or _PIPE_SYSTEMS["san"])
            props.setdefault("dia_in", spec["dia_in"])
            props["dia_in"] = float(props["dia_in"])
            props.setdefault("material", spec["material"])
            props.setdefault("invert_ft", None)
            props.setdefault("slope_in_ft", None)
            if props["invert_ft"] is not None:
                props["invert_ft"] = float(props["invert_ft"])
            if props["slope_in_ft"] is not None:
                props["slope_in_ft"] = float(props["slope_in_ft"])
        elif kind == "grid":
            props.setdefault("label", "")
            props.setdefault("bubble", "a")
        elif kind == "text":
            props.setdefault("text", "")
            props.setdefault("size", "body")
        elif kind == "room":
            props.setdefault("name", "")
            props.setdefault("number", "")
        elif kind == "callout":
            props.setdefault("detail", "")
            props.setdefault("sheet", "")
        ent = Ent(id=f"e{self._next_id:04d}", kind=kind, ply=str(ply),
                  pts=pts, props=props)
        self._next_id += 1
        self.ents.append(ent)
        self._commit(snap_state)
        return ent

    def remove(self, ids: list) -> int:
        """Remove entities by id.  Doors/windows hosted on a removed wall
        go with it (they have no geometry of their own); the returned count
        includes them."""
        snap_state = self._snapshot()
        doomed = {str(i) for i in ids}
        while True:                       # cascade openings onto dead hosts
            extra = {e.id for e in self.ents
                     if e.kind in ("door", "window")
                     and e.props.get("host") in doomed and e.id not in doomed}
            if not extra:
                break
            doomed |= extra
        before = len(self.ents)
        self.ents = [e for e in self.ents if e.id not in doomed]
        n = before - len(self.ents)
        if n:
            self._commit(snap_state)
        return n

    def update(self, id, pts=None, **props) -> bool:
        """Merge props (and replace pts) on one entity.  A door/window given
        pts re-solves its ``t`` against the host and drops the pts again."""
        ent = self.entity(id)
        if ent is None:
            return False
        snap_state = self._snapshot()
        if "ply" in props:                 # ply is an attribute, not a prop
            ent.ply = str(props.pop("ply"))
        if "t" in props:
            props["t"] = min(1.0, max(0.0, float(props["t"])))
        ent.props.update(props)
        if pts is not None:
            pts = [(float(x), float(y)) for (x, y) in pts]
            if ent.kind in ("door", "window"):
                host = self.entity(ent.props.get("host"))
                if pts and host is not None and len(host.pts) == 2:
                    ent.props["t"] = _solve_t(host.pts[0], host.pts[1], pts[0])
                ent.pts = []
            else:
                ent.pts = pts
        self._commit(snap_state)
        return True

    def move(self, ids: list, dx: float, dy: float) -> int:
        """Translate entities.  A door/window whose host moves too rides
        along for free (it is parametric on t); moved alone, it re-solves t
        against the same host, clamped to 0.05..0.95."""
        snap_state = self._snapshot()
        idset = {str(i) for i in ids}
        dx, dy = float(dx), float(dy)
        moved = 0
        for ent in self.ents:
            if ent.id not in idset:
                continue
            if ent.kind in ("door", "window"):
                host = self.entity(ent.props.get("host"))
                if host is not None and host.id in idset:
                    moved += 1            # host translation carries it
                    continue
                if host is None or len(host.pts) != 2:
                    continue
                t = float(ent.props.get("t", 0.5))
                p = _lerp(host.pts[0], host.pts[1], t)
                ent.props["t"] = _solve_t(host.pts[0], host.pts[1],
                                          (p[0] + dx, p[1] + dy))
                moved += 1
            elif ent.pts:
                ent.pts = [(x + dx, y + dy) for (x, y) in ent.pts]
                moved += 1
        if moved:
            self._commit(snap_state)
        return moved

    def entity(self, id) -> Ent | None:
        for e in self.ents:
            if e.id == id:
                return e
        return None

    def on_ply(self, name: str) -> list:
        return [e for e in self.ents if e.ply == name]

    # -------------------------------------------------------------- plies --

    def ply(self, name: str) -> Ply | None:
        for p in self.plies:
            if p.name == name:
                return p
        return None

    def add_ply(self, ply: Ply) -> Ply:
        if self.ply(ply.name) is not None:
            raise ValueError(f"ply {ply.name!r} already exists")
        snap_state = self._snapshot()
        self.plies.append(ply)
        self._commit(snap_state)
        return ply

    def remove_ply(self, name: str) -> bool:
        """Remove a ply; stranded entities land on G-ANNO (created if it was
        the ply being removed) rather than pointing at nothing."""
        target = self.ply(name)
        if target is None:
            return False
        snap_state = self._snapshot()
        self.plies.remove(target)
        fallback = "G-ANNO"
        if self.ply(fallback) is None:
            if self.plies:
                fallback = self.plies[0].name
            elif any(e.ply == name for e in self.ents):
                self.plies.append(Ply(fallback))
        for e in self.ents:
            if e.ply == name:
                e.ply = fallback
        self._commit(snap_state)
        return True

    def rename_ply(self, old: str, new: str) -> None:
        target = self.ply(old)
        if target is None:
            raise ValueError(f"no ply named {old!r}")
        if new != old and self.ply(new) is not None:
            raise ValueError(f"ply {new!r} already exists")
        snap_state = self._snapshot()
        target.name = new
        for e in self.ents:
            if e.ply == old:
                e.ply = new
        self._commit(snap_state)

    # ------------------------------------------------------------ queries --

    def next_grid_label(self, axis: str) -> str:
        """Next free grid label: axis "num" counts 1, 2, ...; "alpha" runs
        A..Z skipping I/O, then AA, AB, ..."""
        labels = [str(e.props.get("label", "")) for e in self.ents
                  if e.kind == "grid"]
        if axis == "num":
            nums = [int(s) for s in labels if s.isdigit()]
            return str(max(nums) + 1) if nums else "1"
        idxs = [i for s in labels if (i := _alpha_index(s)) is not None]
        return _alpha_label(max(idxs) + 1) if idxs else "A"

    def bounds(self, margin_ft: float = 0.0):
        """(x0, y0, x1, y1) over everything the model would draw (hidden
        plies included — bounds describe the model, not the view), or None
        when there is nothing to measure."""
        ext = _ops_extent(render_ops(self, _all_plies=True))
        if ext is None:
            return None
        m = float(margin_ft)
        return (ext[0] - m, ext[1] - m, ext[2] + m, ext[3] + m)

    def stats(self) -> dict:
        """Tally counts for the live quantity readout."""
        wall_lf = 0.0
        walls = doors = windows = rooms = grids = 0
        fixtures: dict[str, int] = {}
        for e in self.ents:
            if e.kind == "wall" and len(e.pts) == 2:
                walls += 1
                a, b = e.pts
                wall_lf += math.hypot(b[0] - a[0], b[1] - a[1])
            elif e.kind == "door":
                doors += 1
            elif e.kind == "window":
                windows += 1
            elif e.kind == "fixture":
                key = str(e.props.get("stencil", ""))
                fixtures[key] = fixtures.get(key, 0) + 1
            elif e.kind == "room":
                rooms += 1
            elif e.kind == "grid":
                grids += 1
        return {"wall_lf": wall_lf, "walls": walls, "doors": doors,
                "windows": windows, "fixtures": fixtures, "rooms": rooms,
                "grids": grids}

    def wall_segments(self) -> list:
        """Wall centerlines as ((x, y), (x, y)) pairs, for the bridges."""
        return [(tuple(e.pts[0]), tuple(e.pts[1])) for e in self.ents
                if e.kind == "wall" and len(e.pts) == 2]

    # -------------------------------------------------------- persistence --

    def to_dict(self) -> dict:
        return {"planloom_loft": 1, "title": self.title,
                "number": self.number, "scale_ratio": self.scale_ratio,
                "plies": [p.to_dict() for p in self.plies],
                "ents": [e.to_dict() for e in self.ents]}

    def save(self, path: str | None = None) -> None:
        """Atomically write the drawing JSON (``*.loft.json``)."""
        path = path or self.path
        if not path:
            raise ValueError("no path: pass save(path) once, or set .path")
        blob = json.dumps(self.to_dict(), indent=2,
                          sort_keys=True).encode("utf-8")
        _atomic_bytes(blob, path)
        self.path = path
        self.dirty = False

    @classmethod
    def load(cls, path: str) -> "DraftModel":
        """Load a drawing; malformed entities are dropped rather than
        crashing (the file is user-visible JSON)."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or int(data.get("planloom_loft") or 0) != 1:
            raise ValueError(f"{path}: not a Planloom Loft drawing "
                             "(missing planloom_loft marker)")
        model = cls(title=str(data.get("title", "")),
                    number=str(data.get("number", "")),
                    scale_ratio=int(data.get("scale_ratio", 96)))
        if "plies" in data:
            plies = []
            for d in data.get("plies") or []:
                try:
                    plies.append(Ply.from_dict(d))
                except Exception:
                    continue
            model.plies = plies
        ents = []
        for d in data.get("ents") or []:
            try:
                ents.append(Ent.from_dict(d))
            except Exception:
                continue
        model.ents = ents
        model._next_id = 1 + max(
            (int(e.id[1:]) for e in ents
             if e.id[:1] == "e" and e.id[1:].isdigit()), default=0)
        model.path = path
        model.dirty = False
        return model


# ------------------------------------------------------ Plumbline snapping --

@dataclass
class SnapHit:
    x: float
    y: float
    kind: str      # end|mid|x|perp|grid|near|ortho
    label: str     # "endpoint", "midpoint", ...


_SNAP_RANK = {"end": 0, "x": 1, "mid": 2, "perp": 3, "grid": 4, "near": 5}
_SNAP_LABEL = {"end": "endpoint", "mid": "midpoint", "x": "intersection",
               "perp": "perpendicular", "grid": "grid", "near": "nearest",
               "ortho": "ortho"}


def _host_anchor(model: DraftModel, ent: Ent):
    """Live hinge/center point of a door/window on its host wall."""
    host = model.entity(ent.props.get("host"))
    if host is None or len(host.pts) != 2:
        return None
    return _lerp(host.pts[0], host.pts[1], float(ent.props.get("t", 0.5)))


def snap(model: DraftModel, x: float, y: float, tol_ft: float,
         anchor=None, ortho: bool = False, enabled=None,
         polar: bool = False) -> SnapHit | None:
    """Plumbline: best snap point near (x, y) within tol_ft.

    Candidates: wall/line/grid endpoints, fixture insertions and live
    door/window points (kind "end"); segment midpoints ("mid"); pairwise
    segment intersections ("x"); the perpendicular foot from ``anchor``
    ("perp"); the nearest point on a grid line ("grid") or any segment
    ("near").  ``enabled`` limits the kinds considered (None = all).
    Tie-break is priority end > x > mid > perp > grid > near, then
    distance.  With no hit, ``ortho`` + ``anchor`` projects onto the
    0°/90° axes through the anchor (plus the 45s with ``polar=True``)."""
    want = set(_SNAP_RANK) if enabled is None else {str(k) for k in enabled}
    segs: list[tuple] = []       # (a, b, is_grid)
    ends: list[tuple] = []
    for e in model.ents:
        if e.kind == "wall" and len(e.pts) == 2:
            segs.append((e.pts[0], e.pts[1], False))
            ends += e.pts
        elif e.kind == "line" and len(e.pts) >= 2:
            segs += [(a, b, False) for a, b in zip(e.pts, e.pts[1:])]
            ends += e.pts
        elif e.kind == "grid" and len(e.pts) == 2:
            segs.append((e.pts[0], e.pts[1], True))
            ends += e.pts
        elif e.kind == "fixture" and e.pts:
            ends.append(e.pts[0])
        elif e.kind in ("door", "window"):
            p = _host_anchor(model, e)
            if p is not None:
                ends.append(p)

    cands: list[tuple] = []      # (rank, dist, x, y, kind)

    def consider(kind, px, py):
        if kind not in want:
            return
        d = math.hypot(px - x, py - y)
        if d <= tol_ft:
            cands.append((_SNAP_RANK[kind], d, px, py, kind))

    for p in ends:
        consider("end", p[0], p[1])
    for a, b, _g in segs:
        consider("mid", (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    if "x" in want:
        # only pairs that pass near the cursor can cross near the cursor
        near = [s for s in segs
                if math.dist(_closest_on_seg(s[0], s[1], (x, y)), (x, y))
                <= tol_ft]
        for i in range(len(near)):
            for j in range(i + 1, len(near)):
                p = _seg_x(near[i][0], near[i][1], near[j][0], near[j][1])
                if p is not None:
                    consider("x", p[0], p[1])
    if anchor is not None and "perp" in want:
        for a, b, _g in segs:
            f = _perp_foot(a, b, anchor)
            if f is not None:
                consider("perp", f[0], f[1])
    for a, b, is_grid in segs:
        q = _closest_on_seg(a, b, (x, y))
        if is_grid:
            consider("grid", q[0], q[1])
        consider("near", q[0], q[1])

    if cands:
        _rank, _d, px, py, kind = min(cands, key=lambda c: (c[0], c[1]))
        return SnapHit(px, py, kind, _SNAP_LABEL[kind])
    if ortho and anchor is not None:
        ax, ay = float(anchor[0]), float(anchor[1])
        opts = [(x, ay), (ax, y)]
        if polar:
            d1 = ((x - ax) + (y - ay)) / 2.0
            d2 = ((x - ax) - (y - ay)) / 2.0
            opts += [(ax + d1, ay + d1), (ax + d2, ay - d2)]
        px, py = min(opts, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
        return SnapHit(px, py, "ortho", _SNAP_LABEL["ortho"])
    return None


# --------------------------------------------------------- wall geometry ----

def wall_openings(model: DraftModel, wall_id: str) -> list:
    """Merged (t0, t1) opening spans along a wall's centerline, clamped to
    the wall.  Doors span hinge->strike (hand decides direction), windows
    center on t.  The wall render breaks its faces around these spans."""
    wall = model.entity(wall_id)
    if wall is None or wall.kind != "wall":
        return []
    frame = _wall_frame(wall)
    if frame is None:
        return []
    length = frame[2]
    spans = []
    for e in model.ents:
        if e.kind not in ("door", "window") or e.props.get("host") != wall_id:
            continue
        w_t = float(e.props.get("width_in", 36.0)) / 12.0 / length
        t = float(e.props.get("t", 0.5))
        if e.kind == "door":
            if e.props.get("hand", "l") == "l":
                t0, t1 = t, t + w_t
            else:
                t0, t1 = t - w_t, t
        else:
            t0, t1 = t - w_t / 2.0, t + w_t / 2.0
        t0, t1 = max(0.0, t0), min(1.0, t1)
        if t1 - t0 > 1e-9:
            spans.append((t0, t1))
    spans.sort()
    merged: list[tuple] = []
    for t0, t1 in spans:
        if merged and t0 <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], t1))
        else:
            merged.append((t0, t1))
    return merged


def _keep_spans(openings) -> list:
    """Complement of the opening spans over [0, 1]."""
    keep, t = [], 0.0
    for t0, t1 in openings:
        if t0 > t:
            keep.append((t, t0))
        t = max(t, t1)
    if t < 1.0:
        keep.append((t, 1.0))
    return keep


def door_geometry(model: DraftModel, ent: Ent) -> dict:
    """Plan geometry of a door, derived live from its host wall ({} when
    the host is missing or degenerate).  Keys:

    hinge/strike (x, y), leaf ((x,y),(x,y)) — closed jamb to open 90° tip,
    arc (cx, cy, r, a0, a1) sweeping CCW from strike to tip, jambs
    [seg, seg] across the wall thickness at both opening ends, opening
    (t0, t1) on the host, width_ft.  hand "l" strikes toward the wall's b
    end; swing "in" opens to the left of a->b travel."""
    host = model.entity(ent.props.get("host"))
    if host is None or host.kind != "wall":
        return {}
    frame = _wall_frame(host)
    if frame is None:
        return {}
    a, b, length, u, n, half = frame
    w = float(ent.props.get("width_in", 36.0)) / 12.0
    t = float(ent.props.get("t", 0.5))
    sgn_u = 1.0 if ent.props.get("hand", "l") == "l" else -1.0
    sgn_n = 1.0 if ent.props.get("swing", "in") == "in" else -1.0
    t0 = max(0.0, min(t, t + w / length * sgn_u))
    t1 = min(1.0, max(t, t + w / length * sgn_u))
    hinge = _lerp(a, b, t)
    strike = _lerp(a, b, t1 if sgn_u > 0 else t0)
    tip = (hinge[0] + n[0] * w * sgn_n, hinge[1] + n[1] * w * sgn_n)
    ang_s = math.degrees(math.atan2(strike[1] - hinge[1],
                                    strike[0] - hinge[0])) % 360.0
    ang_t = math.degrees(math.atan2(tip[1] - hinge[1],
                                    tip[0] - hinge[0])) % 360.0
    if abs((ang_t - ang_s) % 360.0 - 90.0) < 1e-6:
        a0, a1 = ang_s, ang_t          # already a 90° CCW sweep
    else:
        a0, a1 = ang_t, ang_s
    jambs = []
    for tt in (t0, t1):
        p = _lerp(a, b, tt)
        jambs.append(((p[0] + n[0] * half, p[1] + n[1] * half),
                      (p[0] - n[0] * half, p[1] - n[1] * half)))
    return {"hinge": hinge, "strike": strike, "leaf": (hinge, tip),
            "arc": (hinge[0], hinge[1], w, a0, a1), "jambs": jambs,
            "opening": (t0, t1), "width_ft": w}


def window_geometry(model: DraftModel, ent: Ent) -> dict:
    """Plan geometry of a window centered at t on its host wall ({} when
    hostless/degenerate).  Keys: center (x, y), sills [seg, seg] (the two
    wall-face lines across the opening), jambs [seg, seg], glazing seg (the
    centerline), opening (t0, t1), width_ft."""
    host = model.entity(ent.props.get("host"))
    if host is None or host.kind != "wall":
        return {}
    frame = _wall_frame(host)
    if frame is None:
        return {}
    a, b, length, u, n, half = frame
    w = float(ent.props.get("width_in", 48.0)) / 12.0
    t = float(ent.props.get("t", 0.5))
    t0 = max(0.0, t - w / (2.0 * length))
    t1 = min(1.0, t + w / (2.0 * length))
    p0, p1 = _lerp(a, b, t0), _lerp(a, b, t1)
    off = (n[0] * half, n[1] * half)
    jambs = [((p0[0] + off[0], p0[1] + off[1]),
              (p0[0] - off[0], p0[1] - off[1])),
             ((p1[0] + off[0], p1[1] + off[1]),
              (p1[0] - off[0], p1[1] - off[1]))]
    sills = [((p0[0] + off[0], p0[1] + off[1]),
              (p1[0] + off[0], p1[1] + off[1])),
             ((p0[0] - off[0], p0[1] - off[1]),
              (p1[0] - off[0], p1[1] - off[1]))]
    return {"center": _lerp(a, b, t), "sills": sills, "jambs": jambs,
            "glazing": (p0, p1), "opening": (t0, t1), "width_ft": w}


_MITER_MIN_SIN = math.sin(math.radians(5.0))


def wall_joins(model: DraftModel) -> list:
    """Miter patches at L-joins where EXACTLY two walls share an endpoint.

    Face pairing follows offset-polyline logic: walking through the corner,
    the left faces of both walls miter together and so do the right faces —
    that is what keeps the patch out of the wall bodies.  Walls within ~5°
    of parallel (or collinear continuations) fall back to their butt caps.
    Returns [{"pt", "walls": (id, id), "miters": [pt, pt],
    "segs": [(p, q), ...]}]; segs run face-end -> miter -> face-end."""
    walls = []
    for e in model.ents:
        if e.kind != "wall":
            continue
        frame = _wall_frame(e)
        if frame is not None:
            walls.append((e, frame))
    nodes: dict[tuple, list] = {}
    for e, frame in walls:
        for idx, p in ((0, frame[0]), (1, frame[1])):
            key = (round(p[0] * 10000.0), round(p[1] * 10000.0))
            nodes.setdefault(key, []).append((e, frame, idx))
    out = []
    for members in nodes.values():
        if len(members) != 2:
            continue                     # only clean 2-wall corners in v1
        (ent_a, fr_a, ix_a), (ent_b, fr_b, ix_b) = members
        if ent_a.id == ent_b.id:
            continue
        point = fr_a[0] if ix_a == 0 else fr_a[1]

        def away(frame, idx):
            u = frame[3]
            return (u if idx == 0 else (-u[0], -u[1])), frame[5]

        d_a, h_a = away(fr_a, ix_a)
        d_b, h_b = away(fr_b, ix_b)
        cross = d_a[0] * d_b[1] - d_a[1] * d_b[0]
        if abs(cross) < _MITER_MIN_SIN:
            continue                     # near-parallel: butt caps suffice
        n_a = (-d_a[1], d_a[0])
        n_b = (-d_b[1], d_b[0])
        miters, segs = [], []
        for s_a, s_b in ((-1.0, 1.0), (1.0, -1.0)):
            p_a = (point[0] + n_a[0] * h_a * s_a,
                   point[1] + n_a[1] * h_a * s_a)
            p_b = (point[0] + n_b[0] * h_b * s_b,
                   point[1] + n_b[1] * h_b * s_b)
            m = _line_x(p_a, d_a, p_b, d_b)
            if m is None:
                continue
            miters.append(m)
            segs += [(p_a, m), (m, p_b)]
        if miters:
            out.append({"pt": point, "walls": (ent_a.id, ent_b.id),
                        "miters": miters, "segs": segs})
    return out


# ---------------------------------------------------------------- stencils --

def _rect_ops(x0, y0, x1, y1) -> list:
    return [("l", x0, y0, x1, y0), ("l", x1, y0, x1, y1),
            ("l", x1, y1, x0, y1), ("l", x0, y1, x0, y0)]


#: Symbol library.  ``ops`` are drawn in LOCAL INCHES, origin at the
#: insertion center, +y pointing away from the wall the fixture backs onto.
#: Local vocabulary: ("l", x1,y1,x2,y2), ("c", cx,cy,r), ("a", cx,cy,r,a0,a1)
#: degrees CCW, ("e", cx,cy,rx,ry), ("t", x,y,s).  Sizes are the real plan
#: dimensions the trades expect to see.
STENCILS: dict[str, dict] = {
    "wc": {
        "label": "Water closet, tank type", "cat": "plumbing",
        "w_in": 19.0, "d_in": 28.0,
        # 19x8 tank against the wall + elongated bowl kissing its face
        "ops": _rect_ops(-9.5, -14.0, 9.5, -6.0)
        + [("e", 0.0, 3.5, 7.0, 9.5)],
    },
    "lav": {
        "label": "Lavatory, wall-hung", "cat": "plumbing",
        "w_in": 20.0, "d_in": 18.0,
        "ops": _rect_ops(-10.0, -9.0, 10.0, 9.0)
        + [("e", 0.0, -0.5, 7.5, 5.5), ("l", 0.0, -9.0, 0.0, -6.5)],
    },
    "ur": {
        "label": "Urinal, wall-hung", "cat": "plumbing",
        "w_in": 14.0, "d_in": 14.0,
        "ops": _rect_ops(-7.0, -7.0, 7.0, -2.0)
        + [("e", 0.0, 1.5, 6.0, 5.5)],
    },
    "sink_s": {
        "label": "Sink, single bowl", "cat": "plumbing",
        "w_in": 25.0, "d_in": 22.0,
        "ops": _rect_ops(-12.5, -11.0, 12.5, 11.0)
        + _rect_ops(-10.5, -9.0, 10.5, 9.0) + [("l", 0.0, -11.0, 0.0, -9.0)],
    },
    "sink_d": {
        "label": "Sink, double bowl", "cat": "plumbing",
        "w_in": 33.0, "d_in": 22.0,
        "ops": _rect_ops(-16.5, -11.0, 16.5, 11.0)
        + _rect_ops(-14.5, -9.0, 14.5, 9.0)
        + [("l", 0.0, -9.0, 0.0, 9.0), ("l", 0.0, -11.0, 0.0, -9.0)],
    },
    "df": {
        "label": "Drinking fountain", "cat": "plumbing",
        "w_in": 14.0, "d_in": 12.0,
        "ops": _rect_ops(-7.0, -6.0, 7.0, 6.0)
        + [("e", 0.0, 0.5, 5.5, 4.0), ("l", 0.0, -6.0, 0.0, -3.5)],
    },
    "wh": {
        "label": "Water heater", "cat": "equipment",
        "w_in": 24.0, "d_in": 24.0,
        "ops": [("c", 0.0, 0.0, 12.0), ("t", 0.0, 0.0, "WH")],
    },
    "fd": {
        "label": "Floor drain", "cat": "plumbing",
        "w_in": 6.0, "d_in": 6.0,
        # circle + inscribed square rotated 45° (the classic drain diamond)
        "ops": [("c", 0.0, 0.0, 3.0),
                ("l", 3.0, 0.0, 0.0, 3.0), ("l", 0.0, 3.0, -3.0, 0.0),
                ("l", -3.0, 0.0, 0.0, -3.0), ("l", 0.0, -3.0, 3.0, 0.0)],
    },
    "co": {
        "label": "Cleanout", "cat": "plumbing",
        "w_in": 4.0, "d_in": 4.0,
        "ops": [("c", 0.0, 0.0, 2.0), ("t", 0.0, 0.0, "CO")],
    },
    "hb": {
        "label": "Hose bibb", "cat": "plumbing",
        "w_in": 3.0, "d_in": 5.0,
        # stem through the wall + outward triangle
        "ops": [("l", 0.0, -2.5, 0.0, 0.0),
                ("l", -1.5, 0.0, 1.5, 0.0),
                ("l", -1.5, 0.0, 0.0, 2.5), ("l", 1.5, 0.0, 0.0, 2.5)],
    },
    "shower": {
        "label": "Shower, 36 x 36", "cat": "plumbing",
        "w_in": 36.0, "d_in": 36.0,
        "ops": _rect_ops(-18.0, -18.0, 18.0, 18.0)
        + [("l", -18.0, -18.0, 18.0, 18.0), ("l", -18.0, 18.0, 18.0, -18.0),
           ("c", -13.0, -13.0, 2.0)],
    },
    "tub": {
        "label": "Bathtub, 30 x 60", "cat": "plumbing",
        "w_in": 60.0, "d_in": 30.0,     # long side rides the wall
        "ops": _rect_ops(-30.0, -15.0, 30.0, 15.0)
        + [("l", -21.0, 12.0, 21.0, 12.0), ("l", -21.0, -12.0, 21.0, -12.0),
           ("l", -27.0, -6.0, -27.0, 6.0), ("l", 27.0, -6.0, 27.0, 6.0),
           ("a", 21.0, 6.0, 6.0, 0.0, 90.0),
           ("a", -21.0, 6.0, 6.0, 90.0, 180.0),
           ("a", -21.0, -6.0, 6.0, 180.0, 270.0),
           ("a", 21.0, -6.0, 6.0, 270.0, 360.0),
           ("c", -22.0, 0.0, 1.5)],
    },
    "mop": {
        "label": "Mop sink", "cat": "plumbing",
        "w_in": 24.0, "d_in": 24.0,
        "ops": _rect_ops(-12.0, -12.0, 12.0, 12.0)
        + _rect_ops(-9.0, -9.0, 9.0, 9.0),
    },
    "col_steel": {
        "label": "Column, wide-flange", "cat": "structure",
        "w_in": 8.0, "d_in": 8.0,
        # two flanges (outer + inner face, capped ends) and the web
        "ops": [("l", -4.0, 4.0, 4.0, 4.0), ("l", -4.0, 3.5, 4.0, 3.5),
                ("l", -4.0, -3.5, 4.0, -3.5), ("l", -4.0, -4.0, 4.0, -4.0),
                ("l", -4.0, 3.5, -4.0, 4.0), ("l", 4.0, 3.5, 4.0, 4.0),
                ("l", -4.0, -4.0, -4.0, -3.5), ("l", 4.0, -4.0, 4.0, -3.5),
                ("l", 0.0, -3.5, 0.0, 3.5)],
    },
    "col_conc": {
        "label": "Column, concrete", "cat": "structure",
        "w_in": 12.0, "d_in": 12.0,
        "ops": _rect_ops(-6.0, -6.0, 6.0, 6.0)
        + [("l", -6.0, -6.0, 6.0, 6.0), ("l", -6.0, 6.0, 6.0, -6.0)],
    },
}


def _stencil_model_ops(key: str, x: float, y: float, rot_deg: float,
                       flip: bool, ply: str, weight: str,
                       ltype: str) -> list:
    """Transform a stencil's local-inch ops into tagged model-feet render
    ops.  Flip mirrors local x BEFORE rotation (arc angles mirror with it);
    an ellipse rotated off the 90° grid is polygonized (24 chords) because
    the ellipse render op is axis-aligned by design."""
    try:
        spec = STENCILS[key]
    except KeyError:
        raise ValueError(f"unknown stencil {key!r}; expected one of "
                         f"{sorted(STENCILS)}") from None
    theta = math.radians(float(rot_deg))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    fx = -1.0 if flip else 1.0

    def pt(px, py):
        px *= fx
        return (x + (px * cos_t - py * sin_t) / 12.0,
                y + (px * sin_t + py * cos_t) / 12.0)

    out = []
    for op in spec["ops"]:
        tag = op[0]
        if tag == "l":
            (x1, y1), (x2, y2) = pt(op[1], op[2]), pt(op[3], op[4])
            out.append(("line", x1, y1, x2, y2, ply, weight, ltype))
        elif tag == "c":
            cx, cy = pt(op[1], op[2])
            out.append(("circle", cx, cy, op[3] / 12.0, ply, weight, ltype))
        elif tag == "a":
            cx, cy = pt(op[1], op[2])
            a0, a1 = float(op[4]), float(op[5])
            if flip:
                a0, a1 = 180.0 - a1, 180.0 - a0    # mirror keeps CCW sweep
            out.append(("arc", cx, cy, op[3] / 12.0,
                        (a0 + rot_deg) % 360.0, (a1 + rot_deg) % 360.0,
                        ply, weight, ltype))
        elif tag == "e":
            rot = rot_deg % 180.0
            if min(abs(rot), abs(rot - 180.0)) < 1e-9:
                cx, cy = pt(op[1], op[2])
                out.append(("ellipse", cx, cy, op[3] / 12.0, op[4] / 12.0,
                            ply, weight, ltype))
            elif abs(rot - 90.0) < 1e-9:
                cx, cy = pt(op[1], op[2])
                out.append(("ellipse", cx, cy, op[4] / 12.0, op[3] / 12.0,
                            ply, weight, ltype))
            else:
                pts = [pt(op[1] + op[3] * math.cos(2 * math.pi * i / 24),
                          op[2] + op[4] * math.sin(2 * math.pi * i / 24))
                       for i in range(25)]
                out += [("line", p[0], p[1], q[0], q[1], ply, weight, ltype)
                        for p, q in zip(pts, pts[1:])]
        elif tag == "t":
            tx, ty = pt(op[1], op[2])
            out.append(("text", tx, ty, str(op[3]), "body", ply, "c", 0.0))
    return out


def stencil_ops(key: str, x: float, y: float, rot_deg: float = 0.0,
                flip: bool = False) -> list:
    """Model-space ops (feet) for one stencil at (x, y) — the SAME tuple
    shapes :func:`render_ops` emits, tagged ply "P-FIXT" / weight "medium" /
    linetype "solid", so the GUI can feed them straight to its op renderer
    for the placement ghost."""
    return _stencil_model_ops(key, float(x), float(y), float(rot_deg),
                              bool(flip), "P-FIXT", "medium", "solid")


# ------------------------------------------------------------- rendering ----

def _ops_extent(ops):
    """(x0, y0, x1, y1) over a display list, or None when empty.  Arcs count
    their full circle — conservative on purpose (fit must never clip)."""
    xs: list[float] = []
    ys: list[float] = []
    for op in ops:
        tag = op[0]
        if tag == "line":
            xs += (op[1], op[3])
            ys += (op[2], op[4])
        elif tag in ("circle", "arc"):
            xs += (op[1] - op[3], op[1] + op[3])
            ys += (op[2] - op[3], op[2] + op[3])
        elif tag == "ellipse":
            xs += (op[1] - op[3], op[1] + op[3])
            ys += (op[2] - op[4], op[2] + op[4])
        elif tag == "text":
            xs.append(op[1])
            ys.append(op[2])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _wall_ops(model, ent, weight, ltype) -> list:
    frame = _wall_frame(ent)
    if frame is None:
        return []
    a, b, length, u, n, half = frame
    (f1a, f1b), (f2a, f2b) = offset_pair(a, b, half)
    ops = []
    for t0, t1 in _keep_spans(wall_openings(model, ent.id)):
        if (t1 - t0) * length <= 1e-9:
            continue
        for pa, pb in ((f1a, f1b), (f2a, f2b)):
            p, q = _lerp(pa, pb, t0), _lerp(pa, pb, t1)
            ops.append(("line", p[0], p[1], q[0], q[1],
                        ent.ply, weight, ltype))
    # square butt caps; L-corners additionally get a miter patch
    ops.append(("line", f1a[0], f1a[1], f2a[0], f2a[1],
                ent.ply, weight, ltype))
    ops.append(("line", f1b[0], f1b[1], f2b[0], f2b[1],
                ent.ply, weight, ltype))
    return ops


def _door_ops(model, ent, weight) -> list:
    g = door_geometry(model, ent)
    if not g:
        return []
    (hx, hy), (tx, ty) = g["leaf"]
    cx, cy, r, a0, a1 = g["arc"]
    ops = [("line", hx, hy, tx, ty, ent.ply, weight, "solid"),
           ("arc", cx, cy, r, a0, a1, ent.ply, weight, "solid")]
    for (p, q) in g["jambs"]:
        ops.append(("line", p[0], p[1], q[0], q[1], ent.ply, weight, "solid"))
    return ops


def _window_ops(model, ent, weight) -> list:
    g = window_geometry(model, ent)
    if not g:
        return []
    ops = []
    for (p, q) in g["sills"] + g["jambs"]:
        ops.append(("line", p[0], p[1], q[0], q[1], ent.ply, weight, "solid"))
    p, q = g["glazing"]
    ops.append(("line", p[0], p[1], q[0], q[1], ent.ply, weight, "solid"))
    return ops


def _grid_ops(ent, ratio, weight, ltype) -> list:
    if len(ent.pts) != 2:
        return []
    a, b = ent.pts
    ops = [("line", a[0], a[1], b[0], b[1], ent.ply, weight, ltype)]
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length <= _EPS:
        return ops
    r = GRID_BUBBLE_IN / 2.0 * ratio / 12.0
    label = str(ent.props.get("label", ""))
    which = str(ent.props.get("bubble", "a"))
    for end, (p, q) in (("a", (a, b)), ("b", (b, a))):
        if which not in (end, "both"):
            continue
        d = ((p[0] - q[0]) / length, (p[1] - q[1]) / length)
        cx, cy = p[0] + d[0] * r, p[1] + d[1] * r   # tangent at the line end
        ops.append(("circle", cx, cy, r, ent.ply, weight, "solid"))
        if label:
            ops.append(("text", cx, cy, label, "sub", ent.ply, "c", 0.0))
    return ops


def _dim_ops(ent, ratio, weight) -> list:
    if len(ent.pts) != 3:
        return []
    a, b, w = ent.pts
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= _EPS:
        return []
    u = (dx / length, dy / length)
    n = (-u[1], u[0])
    off = (w[0] - a[0]) * n[0] + (w[1] - a[1]) * n[1]
    if off < 0:
        n, off = (-n[0], -n[1]), -off      # dim line lives on the witness side
    gap = DIM_GAP_IN * ratio / 12.0
    over = DIM_OVERSHOOT_IN * ratio / 12.0
    half_tick = DIM_TICK_IN / 2.0 * ratio / 12.0
    da = (a[0] + n[0] * off, a[1] + n[1] * off)
    db = (b[0] + n[0] * off, b[1] + n[1] * off)
    ops = [("line", da[0], da[1], db[0], db[1], ent.ply, weight, "solid")]
    for p in (a, b):                       # extension lines: gap + overshoot
        ops.append(("line", p[0] + n[0] * gap, p[1] + n[1] * gap,
                    p[0] + n[0] * (off + over), p[1] + n[1] * (off + over),
                    ent.ply, weight, "solid"))
    tick = (u[0] + n[0], u[1] + n[1])
    th = math.hypot(tick[0], tick[1])
    tick = (tick[0] / th * half_tick, tick[1] / th * half_tick)
    for p in (da, db):                     # 45° architectural ticks
        ops.append(("line", p[0] - tick[0], p[1] - tick[1],
                    p[0] + tick[0], p[1] + tick[1],
                    ent.ply, weight, "solid"))
    ang = math.degrees(math.atan2(u[1], u[0]))
    if ang <= -90.0 + 1e-9:
        ang += 180.0                       # keep the text readable
    elif ang > 90.0 + 1e-9:
        ang -= 180.0
    t_off = off + gap + text_model_h("body", ratio) / 2.0
    mx = (a[0] + b[0]) / 2.0 + n[0] * t_off
    my = (a[1] + b[1]) / 2.0 + n[1] * t_off
    ops.append(("text", mx, my, fmt_ftin(length), "body", ent.ply, "c", ang))
    return ops


def _room_ops(ent, ratio, weight) -> list:
    if not ent.pts:
        return []
    x, y = ent.pts[0]

    def ft(v):
        return v * ratio / 12.0

    ops = [("text", x, y + ft(0.12), str(ent.props.get("name", "")),
            "body", ent.ply, "c", 0.0)]
    bw, bh, cy = ft(0.55), ft(0.22), y - ft(0.16)
    x0, x1, y0, y1 = x - bw / 2, x + bw / 2, cy - bh / 2, cy + bh / 2
    for p, q in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                 ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        ops.append(("line", p[0], p[1], q[0], q[1],
                    ent.ply, weight, "solid"))
    ops.append(("text", x, cy, str(ent.props.get("number", "")),
                "sub", ent.ply, "c", 0.0))
    return ops


def _callout_ops(ent, ratio, weight) -> list:
    if not ent.pts:
        return []
    x, y = ent.pts[0]
    r = CALLOUT_DIA_IN / 2.0 * ratio / 12.0
    return [("circle", x, y, r, ent.ply, weight, "solid"),
            ("line", x - r, y, x + r, y, ent.ply, weight, "solid"),
            ("text", x, y + r / 2.0, str(ent.props.get("detail", "")),
             "sub", ent.ply, "c", 0.0),
            ("text", x, y - r / 2.0, str(ent.props.get("sheet", "")),
             "sub", ent.ply, "c", 0.0)]


def _pipe_run_ie(ent) -> tuple | None:
    """(invert_start, invert_end) of a pipe run in feet, or None when the
    run carries no invert+slope context.  Positive slope (in/ft) falls
    toward the LAST vertex, so the end invert is the lower one."""
    inv = ent.props.get("invert_ft")
    slope = ent.props.get("slope_in_ft")
    if inv is None or slope is None or len(ent.pts) < 2:
        return None
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                 for a, b in zip(ent.pts, ent.pts[1:]))
    return float(inv), float(inv) - float(slope) * length / 12.0


def _pipe_ops(ent, ratio, weight, ltype) -> list:
    """Single-line pipe run: the polyline in the ply's weight/linetype, the
    size label ('4"') riding the half-length point on the left of travel,
    and IE (invert elevation) notes at both ends on the right of travel
    when the run has invert+slope context.  Clean drafting, no blobs."""
    pts = ent.pts
    if len(pts) < 2:
        return []
    ops = [("line", a[0], a[1], b[0], b[1], ent.ply, weight, ltype)
           for a, b in zip(pts, pts[1:])]
    lens = [math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in zip(pts, pts[1:])]
    total = sum(lens)
    if total <= _EPS:
        return ops
    from .pipewright import fmt_dia_in       # lazy: no cycle at import time
    half, run, seg_i = total / 2.0, 0.0, len(lens) - 1
    for i, seg_len in enumerate(lens):
        if run + seg_len >= half:
            seg_i = i
            break
        run += seg_len
    a, b = pts[seg_i], pts[seg_i + 1]
    seg_len = max(lens[seg_i], _EPS)
    mx, my = _lerp(a, b, min(1.0, max(0.0, (half - run) / seg_len)))
    u = ((b[0] - a[0]) / seg_len, (b[1] - a[1]) / seg_len)
    n = (-u[1], u[0])
    ang = math.degrees(math.atan2(u[1], u[0]))
    if ang <= -90.0 + 1e-9:
        ang += 180.0                          # keep the label readable
    elif ang > 90.0 + 1e-9:
        ang -= 180.0
    off = text_model_h("body", ratio) * 0.85
    ops.append(("text", mx + n[0] * off, my + n[1] * off,
                fmt_dia_in(ent.props.get("dia_in", 4.0)) + '"',
                "body", ent.ply, "c", ang))
    ie = _pipe_run_ie(ent)
    if ie is not None:
        off = text_model_h("sub", ratio) * 1.1
        ends = ((pts[0], (pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]),
                 ie[0]),
                (pts[-1], (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]),
                 ie[1]))
        for p, d, val in ends:
            dl = math.hypot(d[0], d[1])
            if dl <= _EPS:
                continue
            nn = (-d[1] / dl, d[0] / dl)      # left of travel; IE goes right
            ops.append(("text", p[0] - nn[0] * off, p[1] - nn[1] * off,
                        "IE " + fmt_ftin(val), "sub", ent.ply, "c", 0.0))
    return ops


def _branch_tick(x, y, bearing_deg, r, ply, weight) -> list:
    """Short tick across a junction's branch leg (tee/wye/combo symbol)."""
    t = math.radians(float(bearing_deg))
    px, py = x + math.cos(t) * r * 0.8, y + math.sin(t) * r * 0.8
    nn = (-math.sin(t), math.cos(t))
    h = r * 0.55
    return [("line", px - nn[0] * h, py - nn[1] * h,
             px + nn[0] * h, py + nn[1] * h, ply, weight, "solid")]


def _pipe_fitting_ops(model, shown: set, ratio, style) -> list:
    """Fitting symbols at pipe nodes (Pipewright derives them): elbows as
    the included-angle arc, junction fittings as a tick across the branch
    leg (a tick per leg on a cross), caps as a short double tick across the
    end, cleanouts as a small circled CO, p-traps as a little U.  Only
    fittings touching a drawn pipe render, on that pipe's ply; symbols are
    always solid — a dashed symbol reads as broken linework."""
    from .pipewright import derive_fittings   # lazy: no cycle at import time
    ops: list[tuple] = []
    r = PIPE_SYM_IN * ratio / 12.0
    for fit in derive_fittings(model):
        eid = next((i for i in fit.ent_ids if i in shown), None)
        if eid is None:
            continue
        ply = model.entity(eid).ply
        weight = style(ply)[0]
        x, y = fit.node_xy
        kind = fit.kind
        if kind in ("elbow45", "elbow90") and len(fit.legs_deg) >= 2:
            a0, a1 = fit.legs_deg[0] % 360.0, fit.legs_deg[1] % 360.0
            if (a1 - a0) % 360.0 > 180.0:
                a0, a1 = a1, a0               # draw the minor (inside) sweep
            ops.append(("arc", x, y, r, a0, a1, ply, weight, "solid"))
        elif kind in ("tee", "santee", "wye", "combo") \
                and fit.branch_deg is not None:
            ops += _branch_tick(x, y, fit.branch_deg, r, ply, weight)
        elif kind == "cross":
            for leg in fit.legs_deg:
                ops += _branch_tick(x, y, leg, r, ply, weight)
        elif kind == "cap" and fit.legs_deg:
            t = math.radians(fit.legs_deg[0])
            d = (math.cos(t), math.sin(t))    # from the node into the run
            nn = (-d[1], d[0])
            h = r * 0.6
            for back in (0.0, r * 0.4):       # double tick across the end
                px, py = x - d[0] * back, y - d[1] * back
                ops.append(("line", px - nn[0] * h, py - nn[1] * h,
                            px + nn[0] * h, py + nn[1] * h,
                            ply, weight, "solid"))
        elif kind == "cleanout":
            ops.append(("circle", x, y, r * 0.55, ply, weight, "solid"))
            ops.append(("text", x, y, "CO", "body", ply, "c", 0.0))
        elif kind == "ptrap" and fit.legs_deg:
            a = (fit.legs_deg[0] + 90.0) % 360.0
            ops.append(("arc", x, y, r * 0.7, a, (a + 180.0) % 360.0,
                        ply, weight, "solid"))
    return ops


def render_ops(model: DraftModel, include=("all",), _ratio=None,
               _all_plies: bool = False) -> list:
    """Model -> display list of tagged model-space ops.

    Op shapes (all coordinates in model feet, angles degrees CCW):
    ``("line", x1, y1, x2, y2, ply, weight, ltype)``,
    ``("circle", cx, cy, r, ply, weight, ltype)``,
    ``("arc", cx, cy, r, a0, a1, ply, weight, ltype)``,
    ``("ellipse", cx, cy, rx, ry, ply, weight, ltype)``,
    ``("text", x, y, s, size_key, ply, anchor, angle_deg)`` — anchor "c"
    (centered) or "w" (middle-left); size_key indexes TEXT_SIZES.

    ``include``: ``("all",)`` for everything, a tuple of kinds to filter by
    kind, or ``"ent:<id>"`` entries to emit ONLY those entities.  Invisible
    plies are skipped either way; halftone/locked are GUI concerns (look the
    ply up by the op's ply name).  Paper-relative graphics (bubbles, ticks,
    lettering offsets) are converted at the model's scale_ratio."""
    ratio = int(_ratio or model.scale_ratio)
    ids = {s[4:] for s in include
           if isinstance(s, str) and s.startswith("ent:")}
    kinds = None
    if not ids and "all" not in include:
        kinds = {str(k) for k in include}
    plies = {p.name: p for p in model.plies}

    def ply_visible(name):
        if _all_plies:
            return True
        p = plies.get(name)
        return True if p is None else p.visible

    def style(name):
        p = plies.get(name)
        return (p.weight, p.linetype) if p else ("light", "solid")

    def wanted(ent):
        if ids:
            return ent.id in ids and ply_visible(ent.ply)
        if kinds is not None and ent.kind not in kinds:
            return False
        return ply_visible(ent.ply)

    ops: list[tuple] = []
    shown_walls: set[str] = set()
    shown_pipes: set[str] = set()
    for ent in model.ents:
        if not wanted(ent):
            continue
        weight, ltype = style(ent.ply)
        kind = ent.kind
        if kind == "wall":
            ops += _wall_ops(model, ent, weight, ltype)
            shown_walls.add(ent.id)
        elif kind == "door":
            ops += _door_ops(model, ent, weight)
        elif kind == "window":
            ops += _window_ops(model, ent, weight)
        elif kind == "fixture":
            key = str(ent.props.get("stencil", ""))
            if key in STENCILS and ent.pts:
                ops += _stencil_model_ops(
                    key, ent.pts[0][0], ent.pts[0][1],
                    float(ent.props.get("rot", 0.0)),
                    bool(ent.props.get("flip", False)),
                    ent.ply, weight, ltype)
        elif kind == "line":
            ops += [("line", a[0], a[1], b[0], b[1], ent.ply, weight, ltype)
                    for a, b in zip(ent.pts, ent.pts[1:])]
        elif kind == "grid":
            ops += _grid_ops(ent, ratio, weight, ltype)
        elif kind == "room":
            ops += _room_ops(ent, ratio, weight)
        elif kind == "text":
            if ent.pts:
                ops.append(("text", ent.pts[0][0], ent.pts[0][1],
                            str(ent.props.get("text", "")),
                            str(ent.props.get("size", "body")),
                            ent.ply, "w", 0.0))
        elif kind == "dim":
            ops += _dim_ops(ent, ratio, weight)
        elif kind == "callout":
            ops += _callout_ops(ent, ratio, weight)
        elif kind == "pipe":
            ops += _pipe_ops(ent, ratio, weight, ltype)
            shown_pipes.add(ent.id)
    # miter patches close the L-corners of the walls actually drawn
    for join in wall_joins(model):
        id_a, id_b = join["walls"]
        if id_a in shown_walls and id_b in shown_walls:
            wall_a = model.entity(id_a)
            weight, _lt = style(wall_a.ply)
            ops += [("line", p[0], p[1], q[0], q[1],
                     wall_a.ply, weight, "solid")
                    for p, q in join["segs"]]
    # fitting symbols at the nodes of the pipes actually drawn
    if shown_pipes:
        ops += _pipe_fitting_ops(model, shown_pipes, ratio, style)
    return ops


# --------------------------------------------------------------- plate PDF --

def plate_pdf(model: DraftModel, out_path: str, sheet: str = "ARCH D",
              meta: dict | None = None) -> dict:
    """Export the drawing as a titled plate (PDF, landscape).

    Layout: 1/2" border (heavy), a 1.5"-wide vertical title strip on the
    right edge (plate mark, project, title, PLATE number, scale, date,
    drawn by, sheet size), content centered in the remainder at the model's
    scale — dropping down the scale ladder until it fits (``fit`` False in
    the result when even 1/16" = 1'-0" overflows).  A north arrow and a
    graphic scale bar sized to the plotted ratio sit bottom-left.  The plot
    is monochrome: color is a screen concept, plates are ink.  Returns
    {"scale": label, "fit": bool, "ops": n}; write is atomic."""
    # engine-selectable (PLOOM_PDF_ENGINE): reportlab by default, or Planloom's
    # from-scratch writer.  Plates are not verify.py-gated, so the tiny curve
    # anti-aliasing difference between the two rasterizations is cosmetic.
    if os.environ.get("PLOOM_PDF_ENGINE", "reportlab").lower() == "minipdf":
        from .minipdf.colors import black
        from .minipdf import canvas as rl_canvas
    else:
        from reportlab.lib.colors import black
        from reportlab.pdfgen import canvas as rl_canvas

    meta = dict(meta or {})
    try:
        w_in, h_in = SHEET_SIZES[sheet]
    except KeyError:
        raise ValueError(f"unknown sheet {sheet!r}; expected one of "
                         f"{sorted(SHEET_SIZES)}") from None
    page_w, page_h = w_in * 72.0, h_in * 72.0
    margin, tb_w, pad = 36.0, 108.0, 9.0
    cx0, cy0 = margin, margin
    cx1, cy1 = page_w - margin - tb_w, page_h - margin

    # pick the scale: try the model's, then walk down the ladder.  Paper
    # graphics (bubbles, ticks) grow in model feet as the ratio grows, so
    # every candidate re-renders rather than re-scaling the first pass.
    used_ratio = int(model.scale_ratio)
    ops = render_ops(model, _ratio=used_ratio)
    ext = _ops_extent(ops)
    fit = ext is None
    if not fit:
        for ratio in ([used_ratio]
                      + sorted({r for _, r in SCALES if r > used_ratio})):
            cand = ops if ratio == used_ratio \
                else render_ops(model, _ratio=ratio)
            e = _ops_extent(cand)
            used_ratio, ops, ext = ratio, cand, e
            k = 864.0 / ratio                      # points per model foot
            if e is None or ((e[2] - e[0]) * k <= (cx1 - cx0) - 2 * pad
                             and (e[3] - e[1]) * k <= (cy1 - cy0) - 2 * pad):
                fit = True
                break

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setTitle(f"{model.number} {model.title}".strip() or "Planloom plate")
    c.setStrokeColor(black)
    c.setFillColor(black)

    # border + title strip
    c.setDash([])
    c.setLineWidth(weight_pt("heavy"))
    c.rect(margin, margin, page_w - 2 * margin, page_h - 2 * margin)
    c.setLineWidth(weight_pt("medium"))
    c.line(cx1, margin, cx1, page_h - margin)
    scale_label = _scale_label(used_ratio)
    rows = [
        ("__MARK__", "", 52.0),
        ("PROJECT", str(meta.get("project", "")), 40.0),
        ("TITLE", model.title, 46.0),
        ("PLATE", model.number, 56.0),
        ("SCALE", scale_label, 30.0),
        ("DATE", str(meta.get("date") or date.today().isoformat()), 30.0),
        ("DRAWN BY", str(meta.get("drawn_by", "")), 30.0),
        ("SHEET SIZE", sheet, 30.0),
    ]
    row_y = page_h - margin
    mid_x = (cx1 + page_w - margin) / 2.0
    c.setLineWidth(weight_pt("light"))
    for caption, value, height in rows:
        row_y -= height
        c.line(cx1, row_y, page_w - margin, row_y)
        if caption == "__MARK__":
            c.setFont("Helvetica-Bold", 13)
            c.drawCentredString(mid_x, row_y + height / 2 + 2, "PLANLOOM")
            c.setFont("Helvetica", 5.5)
            c.drawCentredString(mid_x, row_y + 8, "THE LOFT — DRAFTING PLATE")
        else:
            c.setFont("Helvetica", 4.5)
            c.drawString(cx1 + 4, row_y + height - 9, caption)
            c.setFont("Helvetica-Bold" if caption == "PLATE" else "Helvetica",
                      14 if caption == "PLATE" else 8)
            c.drawCentredString(mid_x, row_y + height / 2 - 4,
                                str(value)[:26])

    # content, clipped to its area so a non-fit can never bleed into the
    # title strip
    k = 864.0 / used_ratio
    if ext is not None:
        off_x = cx0 + ((cx1 - cx0) - (ext[2] - ext[0]) * k) / 2 - ext[0] * k
        off_y = cy0 + ((cy1 - cy0) - (ext[3] - ext[1]) * k) / 2 - ext[1] * k
        c.saveState()
        clip = c.beginPath()
        clip.rect(cx0, cy0, cx1 - cx0, cy1 - cy0)
        c.clipPath(clip, stroke=0, fill=0)

        def px(v):
            return off_x + v * k

        def py(v):
            return off_y + v * k

        for op in ops:
            tag = op[0]
            if tag == "text":
                _x, _y, s, size_key, _ply, anchor, ang = op[1:]
                if not s:
                    continue
                size = TEXT_SIZES.get(size_key, TEXT_SIZES["body"]) * 72.0
                c.setFont("Helvetica", size)
                if ang:
                    c.saveState()
                    c.translate(px(_x), py(_y))
                    c.rotate(ang)
                    if anchor == "c":
                        c.drawCentredString(0, -size * 0.36, s)
                    else:
                        c.drawString(0, -size * 0.36, s)
                    c.restoreState()
                elif anchor == "c":
                    c.drawCentredString(px(_x), py(_y) - size * 0.36, s)
                else:
                    c.drawString(px(_x), py(_y) - size * 0.36, s)
                continue
            weight, ltype = op[-2], op[-1]
            c.setLineWidth(weight_pt(weight))
            c.setDash([d * 72.0 for d in LINETYPES.get(ltype, ())])
            if tag == "line":
                c.line(px(op[1]), py(op[2]), px(op[3]), py(op[4]))
            elif tag == "circle":
                c.circle(px(op[1]), py(op[2]), op[3] * k)
            elif tag == "arc":
                r = op[3] * k
                extent = (op[5] - op[4]) % 360.0
                if extent > 1e-9:
                    c.arc(px(op[1]) - r, py(op[2]) - r,
                          px(op[1]) + r, py(op[2]) + r, op[4], extent)
            elif tag == "ellipse":
                c.ellipse(px(op[1]) - op[3] * k, py(op[2]) - op[4] * k,
                          px(op[1]) + op[3] * k, py(op[2]) + op[4] * k)
        c.restoreState()

    # north arrow (model +y is north; paper is y-up, so it points up)
    c.setDash([])
    c.setLineWidth(weight_pt("medium"))
    nx, ny, nr = margin + 34.0, margin + 40.0, 13.0
    c.circle(nx, ny, nr)
    c.line(nx, ny - nr, nx, ny + nr)
    c.line(nx - 4, ny + nr - 7, nx, ny + nr)
    c.line(nx + 4, ny + nr - 7, nx, ny + nr)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(nx, ny + nr + 4, "N")

    # graphic scale bar sized to the ratio that actually plotted
    total_ft = next((t for t in (200, 160, 100, 80, 50, 40, 25, 20, 16,
                                 10, 8, 5, 4, 2, 1) if t * k <= 180.0), None)
    if total_ft:
        bx, by = margin + 62.0, margin + 26.0
        seg = total_ft * k / 4.0
        c.setLineWidth(0.6)
        for i in range(4):
            c.rect(bx + i * seg, by, seg, 5, stroke=1, fill=(i % 2 == 0))
        c.setFont("Helvetica", 5.5)
        c.drawCentredString(bx, by - 7, "0")
        c.drawCentredString(bx + 4 * seg, by - 7, f"{total_ft}'")
        c.setFont("Helvetica", 6)
        c.drawString(bx, by + 9, scale_label)

    c.showPage()
    c.save()
    _atomic_bytes(buf.getvalue(), out_path)
    return {"scale": scale_label, "fit": fit, "ops": len(ops)}


# ---------------------------------------------------------------- DXF R12 ---

_OP_PLY_INDEX = {"line": 5, "circle": 4, "arc": 6, "ellipse": 5, "text": 5}


def to_dxf(model: DraftModel, out_path: str) -> int:
    """ASCII DXF R12 of the visible drawing, model feet as drawing units.

    One LAYER per ply (nearest-ACI color); render ops map to LINE / CIRCLE /
    ARC / TEXT, ellipses to a 24-chord polyline approximation (as LINEs —
    every R12 reader eats those).  Linetypes export CONTINUOUS: dash
    patterns here are paper-space cosmetics, not geometry.  Returns the
    entity count; write is atomic."""
    ops = render_ops(model)
    layer_colors = {p.name: aci_for(p.color) for p in model.plies}
    for op in ops:                          # never reference a missing layer
        layer_colors.setdefault(op[_OP_PLY_INDEX[op[0]]], 7)
    pairs: list[tuple[int, str]] = [
        (0, "SECTION"), (2, "HEADER"),
        (9, "$ACADVER"), (1, "AC1009"),
        (0, "ENDSEC"),
        (0, "SECTION"), (2, "TABLES"),
        (0, "TABLE"), (2, "LAYER"), (70, str(len(layer_colors))),
    ]
    for name, color in layer_colors.items():
        pairs += [(0, "LAYER"), (2, name), (70, "0"), (62, str(color)),
                  (6, "CONTINUOUS")]
    pairs += [(0, "ENDTAB"), (0, "ENDSEC"),
              (0, "SECTION"), (2, "ENTITIES")]
    count = 0
    for op in ops:
        tag = op[0]
        layer = op[_OP_PLY_INDEX[tag]]
        if tag == "line":
            pairs += [(0, "LINE"), (8, layer),
                      (10, f"{op[1]:.4f}"), (20, f"{op[2]:.4f}"),
                      (11, f"{op[3]:.4f}"), (21, f"{op[4]:.4f}")]
            count += 1
        elif tag == "circle":
            pairs += [(0, "CIRCLE"), (8, layer),
                      (10, f"{op[1]:.4f}"), (20, f"{op[2]:.4f}"),
                      (40, f"{op[3]:.4f}")]
            count += 1
        elif tag == "arc":
            pairs += [(0, "ARC"), (8, layer),
                      (10, f"{op[1]:.4f}"), (20, f"{op[2]:.4f}"),
                      (40, f"{op[3]:.4f}"),
                      (50, f"{op[4]:.4f}"), (51, f"{op[5]:.4f}")]
            count += 1
        elif tag == "ellipse":
            cx, cy, rx, ry = op[1:5]
            pts = [(cx + rx * math.cos(2 * math.pi * i / 24),
                    cy + ry * math.sin(2 * math.pi * i / 24))
                   for i in range(25)]
            for p, q in zip(pts, pts[1:]):
                pairs += [(0, "LINE"), (8, layer),
                          (10, f"{p[0]:.4f}"), (20, f"{p[1]:.4f}"),
                          (11, f"{q[0]:.4f}"), (21, f"{q[1]:.4f}")]
                count += 1
        elif tag == "text":
            if not op[3]:
                continue
            pairs += [(0, "TEXT"), (8, layer),
                      (10, f"{op[1]:.4f}"), (20, f"{op[2]:.4f}"),
                      (40, f"{text_model_h(op[4], model.scale_ratio):.4f}"),
                      (50, f"{float(op[7]):.4f}"), (1, str(op[3]))]
            count += 1
    pairs += [(0, "ENDSEC"), (0, "EOF")]
    text = "".join(f"{code}\r\n{value}\r\n" for code, value in pairs)
    _atomic_bytes(text.encode("ascii", errors="replace"), out_path)
    return count


# -------------------------------------------------------------- PNG export --

def to_png(model: DraftModel, out_path: str, dpi: int = 150) -> str:
    """Rasterize the plate to a PNG via the optional PDF rasterizer module.
    Renders :func:`plate_pdf` to a temp file first, so the PNG always shows
    exactly what the plate would."""
    try:
        import fitz                                        # noqa: WPS433
    except ImportError as exc:
        raise RuntimeError(
            "PNG export needs the optional PDF rasterizer module (fitz); "
            "it is not installed — export a PDF plate instead") from exc
    tmp_pdf = out_path + ".plate.tmp.pdf"
    plate_pdf(model, tmp_pdf)
    try:
        doc = fitz.open(tmp_pdf)
        try:
            pix = doc[0].get_pixmap(dpi=int(dpi))
            _atomic_bytes(pix.tobytes("png"), out_path)
        finally:
            doc.close()
    finally:
        try:
            os.remove(tmp_pdf)
        except OSError:
            pass
    return out_path


# ----------------------------------------------------------------- bridges --

def takeoff_lines(model: DraftModel, book=None) -> list:
    """Tally -> Reckoner: walls grouped by type as linear feet, openings /
    fixtures / rooms as counts.  With a Reckoner PriceBook, each line gets
    code / unit_cost / total via ``book.find(label)``."""
    from .reckoner import TakeoffLine

    wall_lf: dict[str, float] = {}
    doors = windows = rooms = 0
    fixtures: dict[str, int] = {}
    for e in model.ents:
        if e.kind == "wall" and len(e.pts) == 2:
            a, b = e.pts
            wtype = str(e.props.get("wtype", ""))
            label = (WALL_TYPES.get(wtype, {}).get("label")
                     or wtype or "Wall")
            wall_lf[label] = (wall_lf.get(label, 0.0)
                              + math.hypot(b[0] - a[0], b[1] - a[1]))
        elif e.kind == "door":
            doors += 1
        elif e.kind == "window":
            windows += 1
        elif e.kind == "fixture":
            key = str(e.props.get("stencil", ""))
            label = STENCILS.get(key, {}).get("label") or key or "Fixture"
            fixtures[label] = fixtures.get(label, 0) + 1
        elif e.kind == "room":
            rooms += 1
    lines = [TakeoffLine(subject=label, kind="length", qty=qty, unit="lf")
             for label, qty in wall_lf.items()]
    if doors:
        lines.append(TakeoffLine(subject="Door", kind="count",
                                 qty=float(doors), unit="ea"))
    if windows:
        lines.append(TakeoffLine(subject="Window", kind="count",
                                 qty=float(windows), unit="ea"))
    lines += [TakeoffLine(subject=label, kind="count", qty=float(n),
                          unit="ea") for label, n in fixtures.items()]
    if rooms:
        lines.append(TakeoffLine(subject="Room", kind="count",
                                 qty=float(rooms), unit="ea"))
    lines.sort(key=lambda ln: (ln.kind, ln.subject.lower(), ln.subject))
    if book is not None:
        for line in lines:
            item = book.find(line.subject)
            if item is not None:
                line.code = item.code
                line.unit_cost = item.unit_cost
                line.total = line.qty * item.unit_cost
    return lines


def to_bim(model: DraftModel, wall_height: float = 10.0, floors: int = 1,
           faces: bool = False):
    """Wall centerlines -> extruded wireframe.  Loft model space IS the
    Fieldstitch world frame (E = x, N = y), so segments pass straight
    through to the extruder.  ``faces=True`` also fills ``model.faces``
    (one quad per wall per floor) for shaded 3D; segments are identical
    either way."""
    from . import extrude
    return extrude.build_model(model.wall_segments(),
                               wall_height=wall_height, floors=floors,
                               faces=faces)


def grid_points(model: DraftModel) -> list:
    """Every grid-line crossing as (x, y, "A/1"), alpha label first then
    numeric, sorted by label — ready to push into Fieldstitch as layout
    control points."""
    grids = [e for e in model.ents if e.kind == "grid" and len(e.pts) == 2]
    out = []

    def order_key(label):
        return (0, label) if label.isalpha() else (1, label.zfill(9))

    for i, g1 in enumerate(grids):
        for g2 in grids[i + 1:]:
            p = _seg_x(g1.pts[0], g1.pts[1], g2.pts[0], g2.pts[1])
            if p is None:
                continue
            l1 = str(g1.props.get("label", ""))
            l2 = str(g2.props.get("label", ""))
            la, lb = sorted((l1, l2), key=order_key)
            out.append((p[0], p[1], f"{la}/{lb}"))
    out.sort(key=lambda t: t[2])
    return out
