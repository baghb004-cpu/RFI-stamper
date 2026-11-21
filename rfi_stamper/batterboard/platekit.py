"""platekit: the Batterboard's plate drawing kit (owner brief section 8).

The kit knows nothing about checks or readers.  It draws what it is handed
onto titled plates (the existing Plates title block, through the repo's
own PDF writer) and keeps an exact MANIFEST of every string and every
role-tagged shape that reached the page, so the self-check can prove that
nothing printed without evidence.  ``plates.py`` decides which plate shows
what; this module only knows how a plate is laid out.

A plate (17 x 11 by default) is the title strip on the right and a content
area holding up to three zones:

* the PLAN viewport: world feet (x = Easting, y = Northing, north up)
  plotted at the largest standard scale that fits, clipped to its zone,
  with the page scale stored for viewer measuring tools;
* a TABLE column on the right: header band 4472C4 with white bold text,
  banded rows D9E1F2, thin rules;
* a STATEMENTS band along the bottom: short wrapped lines, plus the north
  arrow and the graphic scale bar.

The look is a plotted drawing: monochrome plan, ONE accent color that is
used for open items only, no tutorial text.  Every plate is stamped
``FOR REVIEW`` until the set is built with ``issue=True``.

Text on the plan (invariant 3: never cover linework)
----------------------------------------------------
Nothing is ever lettered straight onto the plan.  A :meth:`Plate.tag` (a
short id beside a point) or a :meth:`Plate.note` (a small boxed note block
with a thin leader) is only REQUESTED; when placement is resolved the
finished base page is rendered at 90 dpi and each block goes through the
measured-empty finder (:func:`rfi_stamper.layout.find_spot_near`): its
padded window must hold zero content pixels and miss every block placed
before it.  Open-item (accent) blocks are placed first, then the rest in
the order asked.  A block with no free spot within reach is NOT forced:
it is counted ``list_only`` and its row lives in the table alone.  A note
block is a plain rectangle, never a cloud (invariant 6); its leader is a
thin line and may cross linework, the block may not.

Both methods return a :class:`Placement`.  It is truthy when the block
was placed, and asking it (``bool(p)``, ``if p:``) is what resolves
placement for everything requested so far.  Once anything is resolved the
plan is frozen: tables and statements may still be added, plan geometry
may not (a later line could run under a block already placed).

Coordinates in the manifest are PDF page points, origin bottom-left, y up
(viewer y = page height - y); world coordinates are feet.

GUI-free.  fitz (for the base render only) + numpy + the repo's minipdf.
"""
from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass
from datetime import date

import fitz
import numpy as np

from .. import draft, layout, papertrail
from ..fsutil import atomic_write_bytes
from ..minipdf import canvas as _canvas
from ..minipdf import encoding as _enc
from ..minipdf import metrics as _metrics
from ..minipdf.flow import wrap_text as _wrap_words

# ------------------------------------------------------------------ look --

ACCENT = (0xC5 / 255, 0x5A / 255, 0x11 / 255)       # the ONE accent color: open items only
HEAD_FILL = (0x44 / 255, 0x72 / 255, 0xC4 / 255)    # table header band 4472C4
BAND_FILL = (0xD9 / 255, 0xE1 / 255, 0xF2 / 255)    # banded rows D9E1F2
CONCRETE_GRAY = 0.80                                # concrete fill (the picture check keys on it)
FOR_REVIEW = "FOR REVIEW"
ISSUED = "ISSUED"
MARK = "PLANLOOM"
SUB = "THE BATTERBOARD LAYOUT PLATE"

BLACK = (0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0)

#: sheet sizes in paper inches (w, h): the Loft's landscape sheets plus a
#: portrait letter page for the phone booklet
SHEETS = dict(draft.SHEET_SIZES)
SHEETS["LETTER"] = (8.5, 11)

MARGIN = 36.0            # sheet border inset (the Plates title block)
STRIP_W = 108.0          # title strip width
HEAD_H = 22.0            # band under the top border that carries the stamp
GUTTER = 24.0            # paper room around the plan for grid bubbles
GAP = 8.0                # between the plan and the table column
RENDER_DPI = 90          # the measured-empty finder's resolution (layout.py)

FONT = "Helvetica"
BOLD = "Helvetica-Bold"
TABLE_PT, TABLE_LEAD, TABLE_MAX_LINES = 6.0, 7.2, 3
BAND_PT, BAND_LEAD = 6.5, 8.0
NOTE_PT, NOTE_LEAD, NOTE_MAX_LINES = 5.5, 6.4, 6
NOTE_W = 1.6 * 72.0      # widest note block
TAG_PT = 5.5
BOOK_PT, BOOK_LEAD = 9.0, 11.5
BUBBLE_R = 7.0
GRID_DASH = (18.0, 3.0, 3.0, 3.0)

#: title-strip rows: (caption, height); the first row is the plate mark
STRIP_ROWS = (("__MARK__", 52.0), ("PROJECT", 40.0), ("TITLE", 46.0),
              ("PLATE", 56.0), ("SCALE", 30.0), ("DATE", 30.0),
              ("DRAWN BY", 30.0), ("SHEET SIZE", 30.0), ("STATUS", 30.0))


# ------------------------------------------------------------------ text --

@dataclass
class T:
    """The ONLY thing the kit will draw as text.

    ``kind`` says where the words come from: a fixed ``label`` of the
    plate's own vocabulary, a ``statement`` rendered from a proof record,
    ``data`` copied from produced data (a point name, an element id), an
    exact source ``quote``, or a project ``setting``."""
    s: str
    kind: str = "label"
    proof: str = ""
    src: str = ""


KINDS = ("label", "statement", "data", "quote", "setting")


def label(s) -> T:
    return T(str(s), "label")


def data(s, src: str = "", proof: str = "") -> T:
    return T(str(s), "data", str(proof or ""), str(src or ""))


def setting(s) -> T:
    return T(str(s), "setting")


def statement(st) -> T:
    """From a :class:`papertrail.Statement`."""
    return T(str(st.text), "statement", str(st.proof_id), str(getattr(st, "check", "") or ""))


def quote(q) -> T:
    """From a :class:`papertrail.Quote`: ``"<text>" (<cite>)``."""
    cite = q.cite()
    return T('"' + str(q.text) + '"' + (f" ({cite})" if cite else ""), "quote",
             "", cite)


def _as_t(x) -> T:
    if isinstance(x, T):
        if x.kind not in KINDS:
            raise ValueError(f"unknown text kind {x.kind!r}; expected one of {KINDS}")
        return x
    raise TypeError("the plate kit draws only platekit.T text (label / data / "
                    "statement / quote / setting), got " + type(x).__name__)


def _clean(t: T, warn) -> str:
    """The string as it will be printed: other modules' long dashes made
    plain (an exact quote is left as its source wrote it), white space
    collapsed, anything outside the writer's encoding replaced by ``?``."""
    s = t.s if t.kind == "quote" else papertrail.plain(t.s)
    s = " ".join(str(s).split())
    out = []
    bad = False
    for ch in s:
        if ch != _enc.FALLBACK_CHAR and _enc.to_byte(ch) == _enc.FALLBACK_BYTE:
            out.append(_enc.FALLBACK_CHAR)
            bad = True
        else:
            out.append(ch)
    if bad:
        warn(f"text holds a character the plate font cannot print; replaced "
             f"with '?': {''.join(out)[:60]}")
    return "".join(out)


def _w(s: str, font: str, size: float) -> float:
    return _metrics.string_width(s, font, size)


def _wrap(s: str, font: str, size: float, width: float) -> list:
    """Greedy word wrap (the app's one wrapper) that also breaks a single
    word wider than the line, so nothing ever overflows its cell."""
    out = []
    for ln in _wrap_words(s, font, size, width) or [""]:
        while _w(ln, font, size) > width and len(ln) > 1:
            k = len(ln) - 1
            while k > 1 and _w(ln[:k], font, size) > width:
                k -= 1
            out.append(ln[:k])
            ln = ln[k:]
        out.append(ln)
    return out


def _cut(lines: list, max_lines: int, font: str, size: float, width: float) -> tuple:
    """Keep ``max_lines``; mark a cut with an ellipsis.  ``(lines, cut)``."""
    if len(lines) <= max_lines:
        return lines, False
    keep = lines[:max_lines]
    last = keep[-1]
    while last and _w(last + "...", font, size) > width:
        last = last[:-1]
    keep[-1] = last.rstrip() + "..."
    return keep, True


def _ratio_text(scale_label: str) -> str:
    """``1/8" = 1'-0"`` -> ``1/8 in = 1 ft`` (the stored page scale's
    ratio string; no quote marks inside a PDF string)."""
    s = scale_label.replace("1'-0\"", "1 ft").replace('"', " in")
    return " ".join(s.split())


def _num(v: float) -> str:
    s = f"{float(v):.10f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


# ------------------------------------------------------------- placement --

class Placement:
    """The answer to a :meth:`Plate.tag` / :meth:`Plate.note` request.

    Truthy when the block found a free spot, falsy when the row stays in
    the list only.  Asking is what resolves placement (see the module
    note); until then the request is only queued."""

    __slots__ = ("_plate", "_placed")

    def __init__(self, plate):
        self._plate = plate
        self._placed = None

    @property
    def placed(self) -> bool:
        if self._placed is None:
            self._plate.resolve()
        return bool(self._placed)

    def __bool__(self) -> bool:
        return self.placed

    def __int__(self) -> int:
        return int(self.placed)

    __index__ = __int__

    def __eq__(self, other):
        if isinstance(other, (bool, Placement)):
            return self.placed == bool(other)
        return NotImplemented

    def __hash__(self):
        return hash(self.placed)

    def __repr__(self) -> str:
        return "<placed>" if self.placed else "<list only>"


# ------------------------------------------------------------- the views --

def _rgb(v):
    """A fill / stroke spec -> an (r, g, b) tuple or None."""
    if v is None or v is False:
        return None
    if isinstance(v, (int, float)):
        g = float(v)
        return (g, g, g)
    return (float(v[0]), float(v[1]), float(v[2]))


def _weight(weight) -> float:
    if isinstance(weight, (int, float)):
        return float(weight)
    return draft.weight_pt(str(weight))


class _View:
    """Plan drawing in world feet, shared by a plate's plan viewport and a
    booklet page's crop.  Shapes are stored as page-space ops and replayed
    at emission; shapes drawn with a ``role`` are also recorded, in world
    feet, in the manifest."""

    def __init__(self, page_w: float, page_h: float):
        self.page_w, self.page_h = page_w, page_h
        self.viewport = None            # (x0, y0, x1, y1) page points
        self.scale_label = ""
        self.pt_per_ft = None
        self._ox = self._oy = 0.0
        self._world = None              # the world bbox handed to set_plan
        self._fit = True
        self._plan_ops: list = []
        self._texts: list = []          # manifest text entries
        self._geom: list = []           # manifest geometry entries
        self._warnings: list = []
        self._frozen = False

    # -- transform ---------------------------------------------------------- #
    def _fit_plan(self, world_bbox, vx0, vy0, vx1, vy1, margin_ft, gutter) -> None:
        x0, y0, x1, y1 = (float(v) for v in world_bbox)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        if x1 - x0 < 1.0:
            x0, x1 = x0 - 0.5, x1 + 0.5
        if y1 - y0 < 1.0:
            y0, y1 = y0 - 0.5, y1 + 0.5
        m = max(0.0, float(margin_ft))
        ww, wh = (x1 - x0) + 2 * m, (y1 - y0) + 2 * m
        aw, ah = (vx1 - vx0) - 2 * gutter, (vy1 - vy0) - 2 * gutter
        chosen = None
        for lab, ratio in sorted(draft.SCALES, key=lambda sc: sc[1]):
            k = 864.0 / ratio
            if ww * k <= aw and wh * k <= ah:
                chosen = (lab, ratio)
                break
        if chosen is None:
            chosen = max(draft.SCALES, key=lambda sc: sc[1])
            self._fit = False
            self._warn("the plan does not fit this sheet at the smallest standard "
                       f"scale ({chosen[0]}); it is clipped to its viewport")
        lab, ratio = chosen
        k = 864.0 / ratio
        self.scale_label = lab
        self.pt_per_ft = k
        self.viewport = (vx0, vy0, vx1, vy1)
        self._world = (x0, y0, x1, y1)
        self._ox = vx0 + ((vx1 - vx0) - (x1 - x0) * k) / 2.0 - x0 * k
        self._oy = vy0 + ((vy1 - vy0) - (y1 - y0) * k) / 2.0 - y0 * k

    def W(self, e: float, n: float) -> tuple:
        """World feet -> page points (PDF space, origin bottom-left)."""
        self._need_plan()
        return (self._ox + float(e) * self.pt_per_ft,
                self._oy + float(n) * self.pt_per_ft)

    def _need_plan(self) -> None:
        if self.pt_per_ft is None:
            raise RuntimeError("no plan on this page: call set_plan() before "
                               "drawing in world coordinates")

    def _open(self) -> None:
        self._need_plan()
        if self._frozen:
            raise RuntimeError(
                "the plan is frozen: a tag or note was already placed against "
                "it, and a shape added now could run under that block. Draw "
                "all plan geometry before asking any placement.")

    def _warn(self, msg: str) -> None:
        if msg not in self._warnings:
            self._warnings.append(msg)

    def _text(self, drawn: str, t: T, bbox, where: str, **extra) -> dict:
        ent = {"s": drawn, "kind": t.kind, "proof": t.proof, "src": t.src,
               "bbox": [round(float(v), 2) for v in bbox], "where": where}
        ent.update(extra)
        self._texts.append(ent)
        return ent

    # -- shapes ------------------------------------------------------------- #
    def poly(self, pts, *, fill=None, stroke=0.0, weight="light", dash=None,
             accent=False, closed=True, role="") -> None:
        """A polygon (or an open polyline with ``closed=False``).  ``fill``
        is a gray level, an (r, g, b) or None; ``stroke`` a gray level or
        None for no outline; ``accent=True`` strokes in the accent color."""
        self._open()
        pts = [(float(p[0]), float(p[1])) for p in pts]
        if len(pts) < 2:
            return
        s_rgb = ACCENT if (accent and stroke is not None) else _rgb(stroke)
        f_rgb = _rgb(fill) if closed else None
        if s_rgb is None and f_rgb is None:
            return
        self._plan_ops.append(("poly", [self.W(*p) for p in pts], f_rgb, s_rgb,
                               _weight(weight), tuple(dash or ()), bool(closed)))
        if role:
            self._geom.append({"role": str(role), "kind": "poly",
                               "pts": [[p[0], p[1]] for p in pts],
                               "fill": fill if not isinstance(fill, tuple) else list(fill),
                               "closed": bool(closed), "accent": bool(accent)})

    def line(self, a, b, *, weight="light", dash=None, accent=False, role="") -> None:
        self._open()
        a = (float(a[0]), float(a[1]))
        b = (float(b[0]), float(b[1]))
        self._plan_ops.append(("line", self.W(*a), self.W(*b),
                               ACCENT if accent else BLACK, _weight(weight),
                               tuple(dash or ())))
        if role:
            self._geom.append({"role": str(role), "kind": "line", "a": list(a),
                               "b": list(b), "accent": bool(accent)})

    def circle(self, c, r_ft, *, fill=None, stroke=0.0, weight="light",
               accent=False, role="") -> None:
        self._open()
        c = (float(c[0]), float(c[1]))
        s_rgb = ACCENT if (accent and stroke is not None) else _rgb(stroke)
        f_rgb = _rgb(fill)
        if s_rgb is None and f_rgb is None:
            return
        self._plan_ops.append(("circle", self.W(*c), float(r_ft) * self.pt_per_ft,
                               f_rgb, s_rgb, _weight(weight)))
        if role:
            self._geom.append({"role": str(role), "kind": "circle", "c": list(c),
                               "r": float(r_ft),
                               "fill": fill if not isinstance(fill, tuple) else list(fill),
                               "accent": bool(accent)})

    def arc(self, c, r_ft, a0_deg, a1_deg, *, weight="light", accent=False,
            role="") -> None:
        """A circular arc about ``c`` from ``a0`` to ``a1`` degrees,
        counter-clockwise from +E."""
        self._open()
        c = (float(c[0]), float(c[1]))
        ext = float(a1_deg) - float(a0_deg)
        if abs(ext) < 1e-9:
            return
        self._plan_ops.append(("arc", self.W(*c), float(r_ft) * self.pt_per_ft,
                               float(a0_deg), ext, ACCENT if accent else BLACK,
                               _weight(weight)))
        if role:
            self._geom.append({"role": str(role), "kind": "arc", "c": list(c),
                               "r": float(r_ft), "a0": float(a0_deg),
                               "a1": float(a1_deg), "accent": bool(accent)})

    def grid(self, grid) -> None:
        """A :class:`model.Grid`: every line dash-dotted across the plan
        with a bubble carrying its name at its low end (the south end of a
        line that runs north, the west end of one that runs east)."""
        self._open()
        k = self.pt_per_ft
        x0, y0, x1, y1 = self._world
        pad = 8.0 / k
        bx0, by0, bx1, by1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        for g in getattr(grid, "lines", []) or []:
            a = tuple(getattr(g, "world_a", (0.0, 0.0)))
            b = tuple(getattr(g, "world_b", (0.0, 0.0)))
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
                # ends not set: an axis-aligned line at its own offset
                off = float(getattr(g, "offset_ft", 0.0))
                a, b = (((off, by0), (off, by1)) if g.axis == "x"
                        else ((bx0, off), (bx1, off)))
            seg = _clip_line_to_box(a, b, (bx0, by0, bx1, by1))
            if seg is None:
                seg = (a, b)
            p, q = seg
            # the low end first
            if (g.axis == "x" and q[1] < p[1]) or (g.axis != "x" and q[0] < p[0]):
                p, q = q, p
            pp, qp = self.W(*p), self.W(*q)
            ln = math.hypot(qp[0] - pp[0], qp[1] - pp[1])
            if ln < 1e-6:
                continue
            ux, uy = (qp[0] - pp[0]) / ln, (qp[1] - pp[1]) / ln
            self._plan_ops.append(("line", pp, qp, BLACK, draft.weight_pt("fine"),
                                   GRID_DASH))
            bc = (pp[0] - ux * (BUBBLE_R + 2.0), pp[1] - uy * (BUBBLE_R + 2.0))
            self._plan_ops.append(("circle", bc, BUBBLE_R, WHITE, BLACK,
                                   draft.weight_pt("medium")))
            t = data(str(g.name), src=f"grid:{g.name}")
            s = _clean(t, self._warn)
            size = 6.5
            while size > 4.0 and _w(s, BOLD, size) > 2 * BUBBLE_R - 2.5:
                size -= 0.5
            tw = _w(s, BOLD, size)
            tx, ty = bc[0] - tw / 2.0, bc[1] - size * 0.36
            self._plan_ops.append(("text", tx, ty, s, BOLD, size, BLACK))
            self._text(s, t, (tx, ty - size * 0.2, tx + tw, ty + size * 0.8), "plan")
            self._geom.append({"role": "grid", "kind": "line", "a": list(p),
                               "b": list(q), "name": str(g.name), "accent": False})

    def box(self, world_bbox, t: T) -> None:
        """An open-item location box: an accent RECTANGLE (never a cloud)
        with its number in a small accent tab at its top-left corner."""
        self._open()
        t = _as_t(t)
        x0, y0, x1, y1 = (float(v) for v in world_bbox)
        (px0, py0), (px1, py1) = self.W(min(x0, x1), min(y0, y1)), self.W(max(x0, x1), max(y0, y1))
        grow = 3.0                           # stand off the element's own linework
        px0, py0, px1, py1 = px0 - grow, py0 - grow, px1 + grow, py1 + grow
        self._plan_ops.append(("rect", px0, py0, px1 - px0, py1 - py0, None, ACCENT,
                               draft.weight_pt("medium")))
        s = _clean(t, self._warn)
        size = 6.0
        tw = _w(s, BOLD, size)
        tab_w, tab_h = tw + 5.0, 8.0
        self._plan_ops.append(("rect", px0, py1, tab_w, tab_h, ACCENT, ACCENT,
                               draft.weight_pt("medium")))
        self._plan_ops.append(("text", px0 + 2.5, py1 + 2.0, s, BOLD, size, WHITE))
        self._text(s, t, (px0, py1, px0 + tab_w, py1 + tab_h), "box")
        self._geom.append({"role": "item_box", "kind": "poly",
                           "pts": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                           "fill": None, "closed": True, "accent": True, "item": t.s})

    def marker(self, e: float, n: float, kind: str = "point", accent: bool = False) -> None:
        """A small symbol: ``"point"`` a cross in a circle, ``"control"`` a
        triangle, ``"radius"`` a plus."""
        self._open()
        x, y = self.W(e, n)
        rgb = ACCENT if accent else BLACK
        wt = draft.weight_pt("light")
        if kind == "control":
            r = 4.2
            pts = [(x + r * math.cos(math.radians(a)), y + r * math.sin(math.radians(a)))
                   for a in (90.0, 210.0, 330.0)]
            self._plan_ops.append(("poly", pts, WHITE, rgb, draft.weight_pt("medium"),
                                   (), True))
            self._plan_ops.append(("circle", (x, y), 0.6, rgb, rgb, wt))
        elif kind == "radius":
            self._plan_ops.append(("line", (x - 3.0, y), (x + 3.0, y), rgb, wt, ()))
            self._plan_ops.append(("line", (x, y - 3.0), (x, y + 3.0), rgb, wt, ()))
        elif kind == "point":
            self._plan_ops.append(("circle", (x, y), 2.2, None, rgb, wt))
            self._plan_ops.append(("line", (x - 3.2, y), (x + 3.2, y), rgb, wt, ()))
            self._plan_ops.append(("line", (x, y - 3.2), (x, y + 3.2), rgb, wt, ()))
        else:
            raise ValueError(f"unknown marker kind {kind!r}; expected point | "
                             "control | radius")

    # -- emission ----------------------------------------------------------- #
    def _emit_plan(self, c) -> None:
        if self.viewport is None or not self._plan_ops:
            return
        vx0, vy0, vx1, vy1 = self.viewport
        c.saveState()
        clip = c.beginPath()
        clip.rect(vx0, vy0, vx1 - vx0, vy1 - vy0)
        c.clipPath(clip, stroke=0, fill=0)
        for op in self._plan_ops:
            _exec(c, op)
        c.restoreState()

    def _vp_extras(self) -> bytes:
        """The stored page scale (ISO 32000-1 section 12.9): one viewport
        over the plan with a rectilinear measure dictionary."""
        if self.viewport is None or self.pt_per_ft is None:
            return b""
        vx0, vy0, vx1, vy1 = self.viewport
        nf = "<< /Type /NumberFormat /U (%s) /C %s /D 100 >>"
        txt = ("/VP [ << /Type /Viewport /BBox [%s %s %s %s] /Name (Plan) "
               "/Measure << /Type /Measure /Subtype /RL /R %s /X [ %s ] /D [ %s ] "
               "/A [ %s ] >> >> ]"
               % (_num(vx0), _num(vy0), _num(vx1), _num(vy1),
                  _enc.pdf_string(_ratio_text(self.scale_label)).decode("latin-1"),
                  nf % ("ft", _num(1.0 / self.pt_per_ft)),
                  nf % ("ft", "1"), nf % ("sq ft", "1")))
        return txt.encode("latin-1")


def _clip_line_to_box(a, b, box):
    """The part of the INFINITE line a-b inside ``box`` (Liang-Barsky on
    the unbounded parameter), or None when it misses."""
    x0, y0, x1, y1 = box
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = -math.inf, math.inf
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    if not (t0 < t1) or math.isinf(t0) or math.isinf(t1):
        return None
    return ((a[0] + dx * t0, a[1] + dy * t0), (a[0] + dx * t1, a[1] + dy * t1))


def _exec(c, op) -> None:
    """Replay one stored page-space op on a canvas."""
    tag = op[0]
    if tag == "text":
        _x, _y, s, font, size, rgb = op[1:]
        c.setFillColorRGB(*rgb)
        c.setFont(font, size)
        c.drawString(_x, _y, s)
        return
    if tag == "poly":
        pts, f_rgb, s_rgb, wt, dash, closed = op[1:]
        _style(c, f_rgb, s_rgb, wt, dash)
        cc = c._c
        cc.move_to(*pts[0])
        for p in pts[1:]:
            cc.line_to(*p)
        if closed:
            cc.close()
        _paint(cc, f_rgb, s_rgb)
    elif tag == "line":
        a, b, s_rgb, wt, dash = op[1:]
        _style(c, None, s_rgb, wt, dash)
        c.line(a[0], a[1], b[0], b[1])
    elif tag == "circle":
        ctr, r, f_rgb, s_rgb, wt = op[1:]
        _style(c, f_rgb, s_rgb, wt, ())
        c.circle(ctr[0], ctr[1], r, stroke=1 if s_rgb else 0, fill=1 if f_rgb else 0)
    elif tag == "arc":
        ctr, r, a0, ext, s_rgb, wt = op[1:]
        _style(c, None, s_rgb, wt, ())
        c.arc(ctr[0] - r, ctr[1] - r, ctr[0] + r, ctr[1] + r, a0, ext)
    elif tag == "rect":
        x, y, w, h, f_rgb, s_rgb, wt = op[1:]
        _style(c, f_rgb, s_rgb, wt, ())
        c.rect(x, y, w, h, stroke=1 if s_rgb else 0, fill=1 if f_rgb else 0)
    else:                                           # pragma: no cover
        raise ValueError(f"unknown plate op {tag!r}")


def _style(c, f_rgb, s_rgb, wt, dash) -> None:
    c.setLineWidth(wt)
    c.setDash(list(dash))
    if s_rgb is not None:
        c.setStrokeColorRGB(*s_rgb)
    if f_rgb is not None:
        c.setFillColorRGB(*f_rgb)


def _paint(cc, f_rgb, s_rgb) -> None:
    if f_rgb is not None and s_rgb is not None:
        cc.fill_stroke()
    elif f_rgb is not None:
        cc.fill()
    else:
        cc.stroke()


# -------------------------------------------------------------- the plate --

class Plate(_View):
    """One titled page of a :class:`PlateSet`."""

    def __init__(self, owner: "PlateSet", number: str, title: str):
        super().__init__(owner.page_w, owner.page_h)
        self._set = owner
        self.number = data(str(number), src="plate")
        self.title = data(str(title), src="plate")
        self.cx0, self.cy0 = MARGIN, MARGIN
        self.cx1, self.cy1 = owner.page_w - MARGIN - STRIP_W, owner.page_h - MARGIN
        self._table_w = self.cx1 - self.cx0          # no plan: tables own the width
        self._band_h = 64.0
        self._has_plan = False
        self._sheet_ops: list = []       # tables, statements, furniture: outside the plan
        self._overlay_ops: list = []     # placed notes and tags
        self._table_y = None             # next free top edge in the table column
        self._full_y = None
        self._band_used = 0              # statement columns already filled
        self._band_y = None
        self._band_x = None
        self._furn_w = 0.0
        self._pending: list = []         # placement requests not resolved yet
        self._occupied: list = []        # px windows claimed by placed blocks
        self._ii = None                  # the base render's integral image
        self._img = (0, 0)
        self._counts = {"notes": {"asked": 0, "placed": 0, "list_only": 0},
                        "tags": {"asked": 0, "placed": 0, "list_only": 0}}
        self._blocks: list = []          # manifest: placed blocks
        self._strip_done = False

    # -- layout ------------------------------------------------------------- #
    def set_plan(self, world_bbox, *, table_w: float = 330.0, band_h: float = 64.0,
                 margin_ft: float = 3.0) -> None:
        """Give the page a plan.  Reserves a table column ``table_w`` wide
        on the right and a statements band ``band_h`` tall along the
        bottom, then plots ``world_bbox`` (plus ``margin_ft``) at the
        LARGEST standard scale that fits what is left."""
        if self._has_plan:
            raise RuntimeError("this plate already has a plan")
        if self._sheet_ops:
            raise RuntimeError("set_plan() must come before tables and statements")
        self._table_w = max(0.0, float(table_w))
        self._band_h = max(0.0, float(band_h))
        vx0 = self.cx0
        vx1 = self.cx1 - (self._table_w + GAP if self._table_w else 0.0)
        vy0 = self.cy0 + self._band_h
        vy1 = self.cy1 - HEAD_H
        if vx1 - vx0 < 4 * GUTTER or vy1 - vy0 < 4 * GUTTER:
            raise ValueError("the table column and the statements band leave no "
                             "room for a plan on this sheet")
        self._fit_plan(world_bbox, vx0, vy0, vx1, vy1, margin_ft, GUTTER)
        self._has_plan = True

    # -- text on the plan: only through the measured-empty finder ----------- #
    def tag(self, e: float, n: float, t: T, *, accent: bool = False,
            reach_in: float = 0.6) -> Placement:
        """Request a short unboxed tag (an id) beside the point (e, n)."""
        self._need_plan()
        t = _as_t(t)
        s = _clean(t, self._warn)
        p = Placement(self)
        self._counts["tags"]["asked"] += 1
        self._pending.append({"what": "tag", "anchor": self.W(e, n), "world": (float(e), float(n)),
                              "t": t, "lines": [s], "accent": bool(accent),
                              "reach": float(reach_in) * 72.0, "p": p,
                              "w": _w(s, BOLD, TAG_PT), "h": TAG_PT + 0.8, "cut": False})
        return p

    def note(self, e: float, n: float, lines, *, accent: bool = False,
             reach_in: float = 1.75) -> Placement:
        """Request a small boxed note block with a thin leader to (e, n).
        ``lines`` is a list of :class:`T`; each is wrapped to the block."""
        self._need_plan()
        items = [_as_t(x) for x in lines]
        padx, pady = 2.5, 2.0
        inner = NOTE_W - 2 * padx
        rows = []                     # (drawn line, index of its T)
        per_t = []
        for i, t in enumerate(items):
            got = _wrap(_clean(t, self._warn), FONT, NOTE_PT, inner)
            per_t.append(got)
            rows += [(ln, i) for ln in got]
        cut = False
        if len(rows) > NOTE_MAX_LINES:
            rows = rows[:NOTE_MAX_LINES]
            last, li = rows[-1]
            while last and _w(last + "...", FONT, NOTE_PT) > inner:
                last = last[:-1]
            rows[-1] = (last.rstrip() + "...", li)
            cut = True
            self._warn("a note block was cut to "
                       f"{NOTE_MAX_LINES} lines: {items[0].s[:50]}")
        w = max((_w(ln, FONT, NOTE_PT) for ln, _i in rows), default=0.0) + 2 * padx
        h = len(rows) * NOTE_LEAD + 2 * pady
        p = Placement(self)
        self._counts["notes"]["asked"] += 1
        self._pending.append({"what": "note", "anchor": self.W(e, n),
                              "world": (float(e), float(n)), "items": items,
                              "rows": rows, "accent": bool(accent),
                              "reach": float(reach_in) * 72.0, "p": p, "w": w, "h": h,
                              "cut": cut, "padx": padx, "pady": pady})
        return p

    def resolve(self) -> None:
        """Place every tag and note requested so far: open-item (accent)
        blocks first, then the rest in the order asked.  Freezes the plan."""
        if not self._pending:
            return
        self._freeze()
        todo = sorted(enumerate(self._pending), key=lambda kv: (not kv[1]["accent"], kv[0]))
        self._pending = []
        s = RENDER_DPI / 72.0
        img_w, img_h = self._img
        vx0, vy0, vx1, vy1 = self.viewport
        inside = (vx0 * s, (self.page_h - vy1) * s, vx1 * s, (self.page_h - vy0) * s)
        for _k, rq in todo:
            ax, ay = rq["anchor"]
            got = layout.find_spot_near(
                self._ii, img_w, img_h, rq["w"], rq["h"], s, self._occupied,
                (ax * s, (self.page_h - ay) * s), rq["reach"] * s, inside=inside)
            kind = "notes" if rq["what"] == "note" else "tags"
            if got is None:
                rq["p"]._placed = False
                self._counts[kind]["list_only"] += 1
                continue
            x, ytop, win = got
            self._occupied.append(win)
            rq["p"]._placed = True
            self._counts[kind]["placed"] += 1
            self._draw_block(rq, x, ytop, win)

    def _draw_block(self, rq, x: float, ytop: float, win) -> None:
        rgb = ACCENT if rq["accent"] else BLACK
        w, h = rq["w"], rq["h"]
        y0 = ytop - h
        bbox = (x, y0, x + w, ytop)
        if rq["what"] == "tag":
            s = rq["lines"][0]
            self._overlay_ops.append(("text", x, y0 + 1.2, s, BOLD, TAG_PT, rgb))
            self._text(s, rq["t"], bbox, "tag", anchor=[round(v, 4) for v in rq["world"]])
            self._blocks.append({"what": "tag", "bbox": [round(v, 2) for v in bbox],
                                 "window": list(win), "accent": rq["accent"]})
            return
        ax, ay = rq["anchor"]
        ex = min(max(ax, x), x + w)                 # nearest point of the block
        ey = min(max(ay, y0), ytop)
        # leader first, the block's white fill then covers its near end
        self._overlay_ops.append(("line", (ax, ay), (ex, ey), rgb, 0.3, ()))
        self._overlay_ops.append(("rect", x, y0, w, h, WHITE, rgb, 0.4))
        ty = ytop - rq["pady"] - NOTE_PT * 0.82
        per: dict = {}
        for ln, i in rq["rows"]:
            self._overlay_ops.append(("text", x + rq["padx"], ty, ln, FONT, NOTE_PT, rgb))
            per.setdefault(i, []).append(ln)
            ty -= NOTE_LEAD
        for i, t in enumerate(rq["items"]):
            lines = per.get(i)
            if not lines:
                continue
            extra = {"anchor": [round(v, 4) for v in rq["world"]]}
            if len(lines) > 1:
                extra["lines"] = lines
            drawn = " ".join(lines)
            if rq["cut"] and drawn != _clean(t, lambda _m: None):
                extra["cut"] = True
                extra["full"] = _clean(t, lambda _m: None)
            self._text(drawn, t, bbox, "note", **extra)
        self._blocks.append({"what": "note", "bbox": [round(v, 2) for v in bbox],
                             "window": list(win), "accent": rq["accent"],
                             "leader": [[round(ax, 2), round(ay, 2)],
                                        [round(ex, 2), round(ey, 2)]]})

    def _freeze(self) -> None:
        """Render the base page once and build the finder's integral image."""
        if self._ii is not None:
            return
        self._frozen = True
        buf = io.BytesIO()
        c = _canvas.Canvas(buf, pagesize=(self.page_w, self.page_h))
        self._emit(c, overlay=False)
        c.showPage()
        c.save()
        doc = fitz.open(stream=buf.getvalue(), filetype="pdf")
        try:
            pix = doc[0].get_pixmap(dpi=RENDER_DPI, colorspace=fitz.csGRAY, alpha=False)
            gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            self._ii = layout.integral(gray)
            self._img = (pix.width, pix.height)
        finally:
            doc.close()

    # -- sheet furniture ---------------------------------------------------- #
    def table(self, columns, rows, *, title=None, where: str = "right") -> int:
        """Draw a table and return HOW MANY body rows were drawn (the
        caller continues the rest on another plate).  ``columns`` is
        ``[(T header, weight, "l" | "c" | "r")]``; ``rows`` a list of rows
        of :class:`T`.  ``where="right"`` uses the table column beside the
        plan, ``"full"`` the whole content area (a list-only plate)."""
        if where not in ("right", "full"):
            raise ValueError(f"unknown table position {where!r}; expected right | full")
        if where == "right" and self._has_plan and self._table_w <= 0:
            raise ValueError("this plate was laid out with no table column")
        if where == "full" and self._has_plan:
            raise ValueError("a full-width table cannot share a plate with a plan")
        top0 = self.cy1 - HEAD_H - 2.0
        bottom = self.cy0 + (0.0 if self._has_plan else self._band_h) + 6.0
        if where == "right" and self._has_plan:
            x0, x1 = self.cx1 - self._table_w, self.cx1 - 6.0
            y = self._table_y if self._table_y is not None else top0
        else:
            x0, x1 = self.cx0 + 8.0, self.cx1 - 8.0
            y = self._full_y if self._full_y is not None else top0
        cols = [(_as_t(h), float(wt), str(al)) for h, wt, al in columns]
        if not cols:
            return 0
        tot = sum(wt for _h, wt, _a in cols) or 1.0
        widths = [(x1 - x0) * wt / tot for _h, wt, _a in cols]
        pad = 3.0
        ops, texts = [], []

        def cell_lines(t, width, font):
            full = _clean(t, self._warn)
            lines, cut = _cut(_wrap(full, font, TABLE_PT, max(4.0, width - 2 * pad)),
                              TABLE_MAX_LINES, font, TABLE_PT, max(4.0, width - 2 * pad))
            return full, lines, cut

        def put_row(cells, fonts, y_top, fill, rgb, where_kind="table"):
            n_lines = max(len(ln) for _f, ln, _c in cells)
            row_h = n_lines * TABLE_LEAD + 4.0
            if fill is not None:
                ops.append(("rect", x0, y_top - row_h, x1 - x0, row_h, fill, None, 0.0))
            cx = x0
            for (t, (full, lines, cut), width, (_h, _wt, al), font) in zip(
                    cells_t, cells, widths, cols, fonts):
                ty = y_top - 2.0 - TABLE_PT * 0.82
                for ln in lines:
                    tw = _w(ln, font, TABLE_PT)
                    tx = (cx + pad if al == "l" else
                          cx + (width - tw) / 2.0 if al == "c" else cx + width - pad - tw)
                    ops.append(("text", tx, ty, ln, font, TABLE_PT, rgb))
                    ty -= TABLE_LEAD
                extra = {}
                if len(lines) > 1:
                    extra["lines"] = list(lines)
                if cut:
                    extra["cut"] = True
                    extra["full"] = full
                    self._warn(f"a table cell was cut to {TABLE_MAX_LINES} lines: "
                               f"{full[:50]}")
                texts.append((" ".join(lines), t,
                              (cx, y_top - row_h, cx + width, y_top), where_kind, extra))
                cx += width
            return row_h

        # title
        if title is not None:
            tt = title if isinstance(title, T) else label(str(title))
            ts = _clean(tt, self._warn)
            if y - 10.0 < bottom:
                return 0
            ops.append(("text", x0, y - 8.0, ts, BOLD, 7.5, BLACK))
            texts.append((ts, tt, (x0, y - 10.0, x0 + _w(ts, BOLD, 7.5), y), "table", {}))
            y -= 12.0
        # header
        cells_t = [h for h, _wt, _a in cols]
        head = [cell_lines(h, wd, BOLD) for h, wd in zip(cells_t, widths)]
        head_h = max(len(ln) for _f, ln, _c in head) * TABLE_LEAD + 4.0
        body = []
        for row in rows:
            rt = [_as_t(x) for x in row]
            if len(rt) != len(cols):
                raise ValueError(f"a table row has {len(rt)} cells for {len(cols)} columns")
            body.append(rt)
        if not body:
            first_h = 0.0
        else:
            first = [cell_lines(t, wd, FONT) for t, wd in zip(body[0], widths)]
            first_h = max(len(ln) for _f, ln, _c in first) * TABLE_LEAD + 4.0
        if y - head_h - first_h < bottom:
            return 0                                  # not even the first row fits
        table_top = y
        put_row(head, [BOLD] * len(cols), y, HEAD_FILL, WHITE)
        y -= head_h
        rules = [y]
        drawn = 0
        for i, rt in enumerate(body):
            cells = [cell_lines(t, wd, FONT) for t, wd in zip(rt, widths)]
            row_h = max(len(ln) for _f, ln, _c in cells) * TABLE_LEAD + 4.0
            if y - row_h < bottom:
                break
            cells_t = rt
            put_row(cells, [FONT] * len(cols), y, BAND_FILL if i % 2 == 0 else None, BLACK)
            y -= row_h
            rules.append(y)
            drawn += 1
        # thin rules: row lines, column lines, the outline
        thin = draft.weight_pt("fine")
        for ry in rules:
            ops.append(("line", (x0, ry), (x1, ry), BLACK, thin, ()))
        cx = x0
        for width in widths[:-1]:
            cx += width
            ops.append(("line", (cx, table_top), (cx, y), BLACK, thin, ()))
        ops.append(("rect", x0, y, x1 - x0, table_top - y, None, BLACK,
                    draft.weight_pt("light")))
        self._sheet_ops += ops
        for s, t, bbox, wk, extra in texts:
            self._text(s, t, bbox, wk, **extra)
        if where == "right" and self._has_plan:
            self._table_y = y - 10.0
        else:
            self._full_y = y - 10.0
        return drawn

    def statements(self, items) -> int:
        """The bottom band: short lines, wrapped, 6.5 pt.  Returns how many
        of ``items`` fit (in order)."""
        if self._band_h <= 0:
            return 0
        x0 = self.cx0 + 6.0 + self._furn_w
        x1 = (self.cx1 - (self._table_w + GAP if self._has_plan and self._table_w else 0.0)
              - 6.0)
        top = self.cy0 + self._band_h - 5.0
        bottom = self.cy0 + 4.0
        if x1 - x0 < 60.0:
            return 0
        n_cols = max(1, int((x1 - x0) // 330.0))
        col_w = (x1 - x0 - (n_cols - 1) * 12.0) / n_cols
        if self._band_y is None:
            self._band_y = top
            self._band_x = 0
            self._sheet_ops.append(("line", (self.cx0, self.cy0 + self._band_h),
                                    (x1 + 6.0, self.cy0 + self._band_h), BLACK,
                                    draft.weight_pt("fine"), ()))
        done = 0
        for it in items:
            t = _as_t(it)
            font = FONT
            lines = _wrap(_clean(t, self._warn), font, BAND_PT, col_w)
            need = len(lines) * BAND_LEAD + 1.5
            if need > top - bottom:
                break                                  # can never fit this band
            if self._band_y - need < bottom:
                if self._band_x + 1 >= n_cols:
                    break
                self._band_x += 1
                self._band_y = top
            cx = x0 + self._band_x * (col_w + 12.0)
            y = self._band_y
            for ln in lines:
                self._sheet_ops.append(("text", cx, y - BAND_PT * 0.82, ln, font,
                                        BAND_PT, BLACK))
                y -= BAND_LEAD
            extra = {"lines": list(lines)} if len(lines) > 1 else {}
            self._text(" ".join(lines), t, (cx, y, cx + col_w, self._band_y), "band", **extra)
            self._band_y = y - 1.5
            done += 1
        return done

    def furniture(self) -> None:
        """The north arrow and the graphic scale bar for the plan, drawn as
        the Loft's plates draw them: in the statements band when the plate
        has one, else in the plan's bottom-left corner."""
        self._need_plan()
        if self._furn_w or any(op[0] == "furn" for op in self._plan_ops):
            return
        k = self.pt_per_ft
        in_band = self._band_h >= 60.0
        if in_band:
            if self._band_y is not None:
                raise RuntimeError("furniture() must come before statements()")
            bx0, by0 = self.cx0, self.cy0
        else:
            self._open()
            bx0, by0 = self.viewport[0], self.viewport[1]
        ops = []
        med = draft.weight_pt("medium")
        nx, ny, nr = bx0 + 34.0, by0 + 40.0, 13.0
        if not in_band:
            ny = by0 + 24.0
        ops.append(("circle", (nx, ny), nr, None, BLACK, med))
        ops.append(("line", (nx, ny - nr), (nx, ny + nr), BLACK, med, ()))
        ops.append(("line", (nx - 4, ny + nr - 7), (nx, ny + nr), BLACK, med, ()))
        ops.append(("line", (nx + 4, ny + nr - 7), (nx, ny + nr), BLACK, med, ()))
        tn = label("N")
        tw = _w("N", BOLD, 8)
        ops.append(("text", nx - tw / 2.0, ny + nr + 4, "N", BOLD, 8, BLACK))
        self._text("N", tn, (nx - tw / 2.0, ny + nr + 2, nx + tw / 2.0, ny + nr + 11), "plan")
        total_ft = next((t for t in (200, 160, 100, 80, 50, 40, 25, 20, 16, 10, 8, 5,
                                     4, 2, 1) if t * k <= 180.0), None)
        width = 62.0
        if total_ft:
            sx, sy = bx0 + 62.0, ny - 14.0
            seg = total_ft * k / 4.0
            for i in range(4):
                ops.append(("rect", sx + i * seg, sy, seg, 5.0,
                            BLACK if i % 2 == 0 else None, BLACK, 0.6))
            for s, cx in (("0", sx), (f"{total_ft}'", sx + 4 * seg)):
                t = data(s, src="scale")
                tw = _w(s, FONT, 5.5)
                ops.append(("text", cx - tw / 2.0, sy - 7.0, s, FONT, 5.5, BLACK))
                self._text(s, t, (cx - tw / 2.0, sy - 8.5, cx + tw / 2.0, sy - 2.0), "plan")
            t = data(self.scale_label, src="scale")
            s = _clean(t, self._warn)
            ops.append(("text", sx, sy + 9.0, s, FONT, 6, BLACK))
            self._text(s, t, (sx, sy + 7.5, sx + _w(s, FONT, 6), sy + 14.0), "plan")
            width = 62.0 + max(4 * seg + 14.0, _w(s, FONT, 6))
        if in_band:
            self._sheet_ops += ops
            self._furn_w = width + 12.0
        else:
            self._plan_ops += ops

    # -- emission ----------------------------------------------------------- #
    def _strip(self, c, record: bool) -> None:
        proj = self._set.project
        mark_set = bool(str(proj.get("mark") or "").strip())
        mark = setting(proj["mark"]) if mark_set else label(MARK)
        day = proj.get("date")
        vals = {
            "PROJECT": setting(proj.get("project", "") or ""),
            "TITLE": self.title,
            "PLATE": self.number,
            "SCALE": data(self.scale_label, src="scale") if self._has_plan else label("NONE"),
            "DATE": setting(day) if day else data(date.today().isoformat(), src="today"),
            "DRAWN BY": setting(proj.get("drawn_by", "") or ""),
            "SHEET SIZE": setting(self._set.sheet),
            "STATUS": label(ISSUED if self._set.issue else FOR_REVIEW),
        }
        quiet = (lambda _m: None) if not record else self._warn
        rows = [(cap, "" if cap == "__MARK__" else _clean(vals[cap], quiet), h)
                for cap, h in STRIP_ROWS]
        c.setStrokeColorRGB(*BLACK)
        c.setFillColorRGB(*BLACK)
        drawn = draft.draw_title_strip(c, self.page_w, self.page_h, rows, margin=MARGIN,
                                       tb_w=STRIP_W, mark=_clean(mark, quiet), sub=SUB,
                                       fit=True)
        if not record:
            return
        seen_mark = 0
        groups: dict = {}
        for d in drawn:
            bbox = (d["x"], d["y"] - d["size"] * 0.22, d["x"] + d["w"], d["y"] + d["size"] * 0.8)
            if d["row"] == "__MARK__":
                self._text(d["s"], mark if seen_mark == 0 else label(SUB), bbox, "strip")
                seen_mark += 1
            elif d["s"] == d["row"] and d["size"] == 4.5:
                self._text(d["s"], label(d["row"]), bbox, "strip")
            else:
                groups.setdefault(d["row"], []).append((d, bbox))
        for cap, parts in groups.items():
            lines = [d["s"] for d, _b in parts]
            bb = (min(b[0] for _d, b in parts), min(b[1] for _d, b in parts),
                  max(b[2] for _d, b in parts), max(b[3] for _d, b in parts))
            extra = {"lines": lines} if len(lines) > 1 else {}
            if any(d["cut"] for d, _b in parts):
                extra["cut"] = True
                extra["full"] = _clean(vals[cap], lambda _m: None)
                self._warn(f"the title strip cut the {cap} value: {extra['full'][:50]}")
            self._text(" ".join(lines), vals[cap], bb, "strip", **extra)

    def _stamp(self, c, record: bool) -> None:
        if self._set.issue:
            return
        size = 11.0
        tw = _w(FOR_REVIEW, BOLD, size)
        x, y = self.cx0 + 6.0, self.cy1 - HEAD_H + 4.0
        c.setLineWidth(1.2)
        c.setDash([])
        c.setStrokeColorRGB(*BLACK)
        c.setFillColorRGB(*BLACK)
        c.rect(x, y, tw + 10.0, size + 5.0, stroke=1, fill=0)
        c.setFont(BOLD, size)
        c.drawString(x + 5.0, y + 3.6, FOR_REVIEW)
        if record:
            self._text(FOR_REVIEW, label(FOR_REVIEW), (x, y, x + tw + 10.0, y + size + 5.0),
                       "stamp")

    def _emit(self, c, overlay: bool) -> None:
        record = not self._strip_done
        self._strip(c, record)
        self._stamp(c, record)
        self._strip_done = True
        self._emit_plan(c)
        for op in self._sheet_ops:
            _exec(c, op)
        if overlay:
            for op in self._overlay_ops:
                _exec(c, op)
        # leave the canvas in the state the title strip expects
        c.setDash([])
        c.setStrokeColorRGB(*BLACK)
        c.setFillColorRGB(*BLACK)

    def _manifest(self) -> dict:
        return {
            "number": self.number.s, "title": self.title.s,
            "page_w": self.page_w, "page_h": self.page_h,
            "scale": self.scale_label, "pt_per_ft": self.pt_per_ft,
            "viewport": list(self.viewport) if self.viewport else None,
            "transform": ({"ox": self._ox, "oy": self._oy, "k": self.pt_per_ft}
                          if self._has_plan else None),
            "fit": self._fit,
            "texts": list(self._texts), "geometry": list(self._geom),
            "notes": dict(self._counts["notes"]), "tags": dict(self._counts["tags"]),
            "blocks": list(self._blocks), "warnings": list(self._warnings),
        }


# ---------------------------------------------------------------- the set --

class PlateSet:
    """A set of plates on one sheet size, saved as one PDF.

    ``project`` is the project settings dict (``project``, ``drawn_by``,
    ``date``, optional ``mark``): the only source of names on a plate.
    Until ``issue=True`` every plate is stamped FOR REVIEW."""

    def __init__(self, *, sheet: str = "ANSI B", project=None, issue: bool = False):
        try:
            w_in, h_in = SHEETS[sheet]
        except KeyError:
            raise ValueError(f"unknown sheet {sheet!r}; expected one of "
                             f"{sorted(SHEETS)}") from None
        self.sheet = sheet
        self.page_w, self.page_h = w_in * 72.0, h_in * 72.0
        self.project = dict(project or {})
        self.issue = bool(issue)
        self.plates: list = []
        self.manifest: dict = {}
        self.base_pdf: bytes = b""       # the same pages WITHOUT tags and notes

    def plate(self, number: str, title: str) -> Plate:
        p = Plate(self, number, title)
        self.plates.append(p)
        return p

    def _build(self, overlay: bool) -> bytes:
        buf = io.BytesIO()
        c = _canvas.Canvas(buf, pagesize=(self.page_w, self.page_h))
        for p in self.plates:
            p._emit(c, overlay=overlay)
            extras = p._vp_extras() if p._has_plan else b""
            if extras:
                c._page.extras = extras
            c.showPage()
        c.save()
        return buf.getvalue()

    def save(self, path: str) -> dict:
        """Finalize every plate, write the PDF atomically and return the
        manifest (also kept on ``.manifest``)."""
        if not self.plates:
            raise ValueError("a plate set needs at least one plate")
        for p in self.plates:
            p.resolve()
        data_ = self._build(overlay=True)
        self.base_pdf = self._build(overlay=False)
        _self_check(data_, len(self.plates))
        atomic_write_bytes(data_, path)
        self.manifest = {"file": os.path.basename(str(path)), "sheet": self.sheet,
                         "issue": self.issue,
                         "pages": [p._manifest() for p in self.plates]}
        return self.manifest


def _self_check(data_: bytes, n_pages: int) -> None:
    """The writer's strict self-re-parse (recovery disabled) before any
    byte lands on disk."""
    from ..minipdf.parse import MiniReader
    rd = MiniReader(data_, strict=True)
    got = len(rd.pages) if hasattr(rd, "pages") else None
    if got is not None and got != n_pages:
        raise ValueError(f"plate self-check: wrote {n_pages} page(s), re-read {got}")


# ------------------------------------------------------------ the booklet --

class _Crop(_View):
    """The plan crop on one booklet page (the drawing half of :class:`_View`)."""


class Booklet:
    """The phone booklet: one item per LETTER page (612 x 792 portrait),
    a crop of the plan on top and the item's text under it."""

    def __init__(self, *, project=None, issue: bool = False):
        w_in, h_in = SHEETS["LETTER"]
        self.sheet = "LETTER"
        self.page_w, self.page_h = w_in * 72.0, h_in * 72.0
        self.project = dict(project or {})
        self.issue = bool(issue)
        self.pages: list = []
        self.manifest: dict = {}

    def item(self, number: T, title: T, world_bbox, draw, lines) -> None:
        """One page.  ``draw(view)`` draws the crop with the plan methods
        of a plate (poly / line / circle / arc / grid / box / marker / W);
        ``lines`` is the text (list of :class:`T`) printed under it."""
        number, title = _as_t(number), _as_t(title)
        v = _Crop(self.page_w, self.page_h)
        m = MARGIN
        head_y = self.page_h - m - 16.0
        vy1 = head_y - 12.0
        vy0 = vy1 - 0.55 * (self.page_h - 2 * m)
        v._fit_plan(world_bbox, m, vy0, self.page_w - m, vy1, 3.0, GUTTER)
        draw(v)
        ops = []
        # header: number and title
        ns, ts = _clean(number, v._warn), _clean(title, v._warn)
        ops.append(("text", m, head_y, ns, BOLD, 12.0, BLACK))
        v._text(ns, number, (m, head_y - 3, m + _w(ns, BOLD, 12.0), head_y + 10), "strip")
        tx = m + _w(ns, BOLD, 12.0) + 10.0
        t_lines, cut = _cut(_wrap(ts, BOLD, 10.0, self.page_w - m - tx), 1, BOLD, 10.0,
                            self.page_w - m - tx)
        ops.append(("text", tx, head_y, t_lines[0], BOLD, 10.0, BLACK))
        extra = {"cut": True, "full": ts} if cut else {}
        if cut:
            v._warn(f"a booklet title was cut: {ts[:50]}")
        v._text(t_lines[0], title, (tx, head_y - 3, tx + _w(t_lines[0], BOLD, 10.0),
                                    head_y + 10), "strip", **extra)
        ops.append(("rect", m, vy0, self.page_w - 2 * m, vy1 - vy0, None, BLACK,
                    draft.weight_pt("medium")))
        # scale under the crop, right aligned
        st = data(v.scale_label, src="scale")
        ss = _clean(st, v._warn)
        sw = _w(ss, FONT, 6.5)
        ops.append(("text", self.page_w - m - sw, vy0 - 9.0, ss, FONT, 6.5, BLACK))
        v._text(ss, st, (self.page_w - m - sw, vy0 - 11.0, self.page_w - m, vy0 - 3.0), "plan")
        # the lines
        y = vy0 - 24.0
        bottom = m + 26.0
        width = self.page_w - 2 * m
        for it in lines:
            t = _as_t(it)
            font = BOLD if t.kind == "label" else FONT
            got = _wrap(_clean(t, v._warn), font, BOOK_PT, width)
            if y - len(got) * BOOK_LEAD < bottom:
                v._warn(f"a booklet page ran out of room; text left off: {t.s[:50]}")
                break
            y_top = y
            for ln in got:
                ops.append(("text", m, y - BOOK_PT * 0.82, ln, font, BOOK_PT, BLACK))
                y -= BOOK_LEAD
            extra = {"lines": list(got)} if len(got) > 1 else {}
            v._text(" ".join(got), t, (m, y, m + width, y_top), "band", **extra)
            y -= 3.0
        # footer: project settings, status
        foot = []
        if str(self.project.get("project") or "").strip():
            foot.append(setting(self.project["project"]))
        day = self.project.get("date")
        foot.append(setting(day) if day else data(date.today().isoformat(), src="today"))
        fx = m
        for t in foot:
            s = _clean(t, v._warn)
            ops.append(("text", fx, m + 4.0, s, FONT, 6.5, BLACK))
            v._text(s, t, (fx, m + 2.0, fx + _w(s, FONT, 6.5), m + 10.0), "strip")
            fx += _w(s, FONT, 6.5) + 14.0
        if not self.issue:
            size = 9.0
            tw = _w(FOR_REVIEW, BOLD, size)
            sx = self.page_w - m - tw - 10.0
            ops.append(("rect", sx, m, tw + 10.0, size + 5.0, None, BLACK, 1.0))
            ops.append(("text", sx + 5.0, m + 3.4, FOR_REVIEW, BOLD, size, BLACK))
            v._text(FOR_REVIEW, label(FOR_REVIEW), (sx, m, sx + tw + 10.0, m + size + 5.0),
                    "stamp")
        v._sheet = ops
        v._number, v._title = number, title
        self.pages.append(v)

    def save(self, path: str) -> dict:
        if not self.pages:
            raise ValueError("a booklet needs at least one item")
        buf = io.BytesIO()
        c = _canvas.Canvas(buf, pagesize=(self.page_w, self.page_h))
        for v in self.pages:
            c.setStrokeColorRGB(*BLACK)
            c.setFillColorRGB(*BLACK)
            v._emit_plan(c)
            for op in v._sheet:
                _exec(c, op)
            extras = v._vp_extras()
            if extras:
                c._page.extras = extras
            c.showPage()
        c.save()
        data_ = buf.getvalue()
        _self_check(data_, len(self.pages))
        atomic_write_bytes(data_, path)
        self.manifest = {
            "file": os.path.basename(str(path)), "sheet": self.sheet, "issue": self.issue,
            "pages": [{
                "number": v._number.s, "title": v._title.s,
                "page_w": v.page_w, "page_h": v.page_h,
                "scale": v.scale_label, "pt_per_ft": v.pt_per_ft,
                "viewport": list(v.viewport),
                "transform": {"ox": v._ox, "oy": v._oy, "k": v.pt_per_ft},
                "fit": v._fit, "texts": list(v._texts), "geometry": list(v._geom),
                "notes": {"asked": 0, "placed": 0, "list_only": 0},
                "tags": {"asked": 0, "placed": 0, "list_only": 0},
                "blocks": [], "warnings": list(v._warnings),
            } for v in self.pages]}
        return self.manifest
