"""Synthetic plan-set builder: every Batterboard fixture comes from here.

No real project document ever goes in the repository (brief section 9).
:class:`Sheet` writes one PDF page from raw content-stream operators, so a
fixture can contain exactly the constructs the Sightline exists for: a
fill limited by a clip path, linework hidden under a later fill, a
revision cloud across an element, a /Rotate page.

All coordinates given to a :class:`Sheet` are VIEWER points for an
unrotated page (top-left origin, y down), the same space the readers work
in; the builder flips them into PDF space itself.

:func:`build_sample_set` (further down) assembles a whole small building:
structural plans, an architectural plan, general notes, a wall schedule,
a CAD background, a shop drawing and an RFI response.  The tests use it,
and the Batterboard tab offers it as a sample set to try the tool on.
"""
from __future__ import annotations

import math

import fitz

_K = 0.5522847498307936          # cubic Bezier circle constant


def _n(v: float) -> str:
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


class Sheet:
    """One page, drawn with raw operators, in viewer coordinates."""

    def __init__(self, w: float = 792.0, h: float = 612.0):
        self.w, self.h = float(w), float(h)
        self.ops: list = []
        self.texts: list = []        # (x, y, text, size, rgb, rotate)

    # -- helpers ----------------------------------------------------------- #
    def _p(self, pt) -> str:
        return f"{_n(pt[0])} {_n(self.h - pt[1])}"

    def raw(self, op: str) -> "Sheet":
        self.ops.append(op)
        return self

    def _path(self, pts, closed=True) -> str:
        s = f"{self._p(pts[0])} m " + " ".join(f"{self._p(p)} l" for p in pts[1:])
        return s + (" h" if closed else "")

    @staticmethod
    def _rg(color, stroke=False) -> str:
        if isinstance(color, (int, float)):
            return f"{_n(color)} {'G' if stroke else 'g'}"
        r, g, b = color
        return f"{_n(r)} {_n(g)} {_n(b)} {'RG' if stroke else 'rg'}"

    # -- fills --------------------------------------------------------------- #
    def fill_poly(self, pts, color=0.75) -> "Sheet":
        return self.raw(f"{self._rg(color)} {self._path(pts)} f")

    def fill_rect(self, x0, y0, x1, y1, color=0.75) -> "Sheet":
        return self.fill_poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], color)

    def _circle_path(self, c, r) -> str:
        cx, cy = c[0], self.h - c[1]
        k = r * _K
        return (f"{_n(cx + r)} {_n(cy)} m "
                f"{_n(cx + r)} {_n(cy + k)} {_n(cx + k)} {_n(cy + r)} {_n(cx)} {_n(cy + r)} c "
                f"{_n(cx - k)} {_n(cy + r)} {_n(cx - r)} {_n(cy + k)} {_n(cx - r)} {_n(cy)} c "
                f"{_n(cx - r)} {_n(cy - k)} {_n(cx - k)} {_n(cy - r)} {_n(cx)} {_n(cy - r)} c "
                f"{_n(cx + k)} {_n(cy - r)} {_n(cx + r)} {_n(cy - k)} {_n(cx + r)} {_n(cy)} c h")

    def fill_circle(self, c, r, color=0.75) -> "Sheet":
        return self.raw(f"{self._rg(color)} {self._circle_path(c, r)} f")

    # -- strokes ------------------------------------------------------------- #
    def _style(self, width, color, dash) -> str:
        d = "[] 0 d" if not dash else f"[{' '.join(_n(v) for v in dash)}] 0 d"
        return f"{_n(width)} w {self._rg(color, stroke=True)} {d}"

    def line(self, a, b, width=0.5, color=0, dash=None) -> "Sheet":
        return self.raw(f"{self._style(width, color, dash)} "
                        f"{self._p(a)} m {self._p(b)} l S")

    def polyline(self, pts, width=0.5, color=0, closed=False, dash=None) -> "Sheet":
        return self.raw(f"{self._style(width, color, dash)} "
                        f"{self._path(pts, closed)} S")

    def rect(self, x0, y0, x1, y1, width=0.5, color=0, dash=None) -> "Sheet":
        return self.polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], width,
                             color, closed=True, dash=dash)

    def circle(self, c, r, width=0.5, color=0) -> "Sheet":
        return self.raw(f"{self._style(width, color, None)} {self._circle_path(c, r)} S")

    def arc(self, c, r, a0_deg, a1_deg, width=0.5, color=0) -> "Sheet":
        """Circular arc about ``c`` from ``a0`` to ``a1`` (degrees, measured
        counter-clockwise from +x in the viewer's y-DOWN space, i.e. the
        angle of ``(cos a, -sin a)`` on the page)."""
        return self.raw(f"{self._style(width, color, None)} {self._arc_path(c, r, a0_deg, a1_deg)} S")

    def _arc_path(self, c, r, a0_deg, a1_deg, move=True) -> str:
        cx, cy = c[0], self.h - c[1]
        n = max(1, int(math.ceil(abs(a1_deg - a0_deg) / 90.0)))
        step = math.radians(a1_deg - a0_deg) / n
        a = math.radians(a0_deg)
        out = []
        if move:
            out.append(f"{_n(cx + r * math.cos(a))} {_n(cy + r * math.sin(a))} m")
        for _ in range(n):
            b = a + step
            al = math.sin(b - a) * (math.sqrt(4 + 3 * math.tan((b - a) / 2) ** 2) - 1) / 3
            x1 = cx + r * (math.cos(a) - al * math.sin(a))
            y1 = cy + r * (math.sin(a) + al * math.cos(a))
            x2 = cx + r * (math.cos(b) + al * math.sin(b))
            y2 = cy + r * (math.sin(b) - al * math.cos(b))
            out.append(f"{_n(x1)} {_n(y1)} {_n(x2)} {_n(y2)} "
                       f"{_n(cx + r * math.cos(b))} {_n(cy + r * math.sin(b))} c")
            a = b
        return " ".join(out)

    def cloud(self, pts, bump=9.0, width=0.9, color=(0.8, 0.0, 0.0), closed=True) -> "Sheet":
        """A revision cloud: scallop arcs chained along a polyline."""
        path = []
        seq = list(pts) + ([pts[0]] if closed else [])
        first = True
        for a, b in zip(seq, seq[1:]):
            ln = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, int(round(ln / bump)))
            ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
            ang = math.degrees(math.atan2(-uy, ux))      # PDF-space direction
            for i in range(n):
                m = (a[0] + ux * ln * (i + 0.5) / n, a[1] + uy * ln * (i + 0.5) / n)
                path.append(self._arc_path(m, ln / n / 2.0, ang + 180.0, ang, move=first))
                first = False
        return self.raw(f"{self._style(width, color, None)} {' '.join(path)} S")

    # -- clipping ------------------------------------------------------------ #
    def clip_begin(self, pts) -> "Sheet":
        return self.raw(f"q {self._path(pts)} W n")

    def clip_end(self) -> "Sheet":
        return self.raw("Q")

    # -- text ---------------------------------------------------------------- #
    def text(self, x, y, s, size=8.0, color=(0, 0, 0), rotate=0, bold=False) -> "Sheet":
        """Text with its BASELINE-left at (x, y)."""
        self.texts.append((float(x), float(y), str(s), float(size), color, rotate, bold))
        return self

    def text_c(self, x, y, s, size=8.0, color=(0, 0, 0), bold=False) -> "Sheet":
        """Text centered on x (baseline at y)."""
        w = fitz.get_text_length(str(s), "hebo" if bold else "helv", size)
        return self.text(x - w / 2.0, y, s, size, color, 0, bold)

    # -- output -------------------------------------------------------------- #
    def add_to(self, doc, rot: int = 0, upright: bool = False):
        """Append this sheet to an open fitz document; returns the page.

        ``rot`` sets /Rotate.  By default the VIEW is rotated (the sheet
        is seen sideways: a pure coordinate test).  ``upright=True``
        authors the page the way real rotated sets are made: the media is
        turned and the content pre-rotated, so the sheet still reads
        upright in a viewer while every raw coordinate in the file is
        rotated."""
        rot = int(rot) % 360
        if not (upright and rot):
            page = doc.new_page(width=self.w, height=self.h)
            page.insert_text((2, 2), " ", fontsize=1)        # creates the stream + font
            xref = page.get_contents()[0]
            doc.update_stream(xref, ("\n".join(self.ops) + "\n").encode("ascii"))
            for x, y, s, size, color, rotate, bold in self.texts:
                page.insert_text((x, y), s, fontsize=size, color=color, rotate=rotate,
                                 fontname="hebo" if bold else "helv")
            if rot:
                page.set_rotation(rot)
            return page
        if rot == 90:
            mw, mh, cm = self.h, self.w, f"0 1 -1 0 {_n(self.h)} 0 cm"
        elif rot == 270:
            mw, mh, cm = self.h, self.w, f"0 -1 1 0 0 {_n(self.w)} cm"
        else:
            mw, mh, cm = self.w, self.h, f"-1 0 0 -1 {_n(self.w)} {_n(self.h)} cm"
        page = doc.new_page(width=mw, height=mh)
        page.insert_text((2, 2), " ", fontsize=1, fontname="helv")
        page.insert_text((2, 2), " ", fontsize=1, fontname="hebo")
        ops = ["q", cm] + list(self.ops)
        for x, y, s, size, color, rotate, bold in self.texts:
            esc = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            tm = (f"0 1 -1 0 {_n(x)} {_n(self.h - y)} Tm" if rotate == 90
                  else f"1 0 0 1 {_n(x)} {_n(self.h - y)} Tm")
            ops.append(f"BT {self._rg(color)} /{'hebo' if bold else 'helv'} {_n(size)} Tf "
                       f"{tm} ({esc}) Tj ET")
        ops.append("Q")
        xrefs = page.get_contents()
        doc.update_stream(xrefs[0], ("\n".join(ops) + "\n").encode("latin-1"))
        for x in xrefs[1:]:
            doc.update_stream(x, b"\n")
        page.set_rotation(rot)
        return page

    def build(self, rot: int = 0, upright: bool = False):
        """-> ``(doc, page)`` in memory."""
        doc = fitz.open()
        page = self.add_to(doc, rot, upright)
        return doc, page

    def save(self, path: str, rot: int = 0, upright: bool = False) -> str:
        doc, _page = self.build(rot, upright)
        _save(doc, path)
        return path


def _save(doc, path: str) -> None:
    """Save a fixture with EMPTY metadata (no tool names ride along)."""
    doc.set_metadata({})
    doc.save(path, garbage=3, deflate=True)
    doc.close()


# ====================================================================== #
#  The sample building                                                   #
# ====================================================================== #
#
#  A small two-level concrete building, described once in WORLD feet
#  (x = Easting, y = Northing, grid A/1 at the origin) and drawn onto
#  17 x 11 sheets at 1/8" = 1'-0".  Every number a test asserts against
#  lives in the tables below.

PPF = 9.0                         # points per foot at 1/8" = 1'-0"
SHEET_W, SHEET_H = 1224.0, 792.0  # 17 x 11 in
SCALE_NOTE = "1/8\" = 1'-0\""
GRAY = 0.75                       # the concrete fill

GRID_X = [("A", 0.0), ("B", 24.0), ("C", 48.0), ("D", 72.0)]     # constant Easting
GRID_Y = [("1", 0.0), ("2", 20.0), ("3", 40.0)]                  # constant Northing

#: slab outline: 1 ft past the grid, the NE corner a 6 ft radius
SLAB_ARC = {"center": (67.0, 35.0), "radius": 6.0}
SLAB_L2 = [(-1.0, -1.0), (73.0, -1.0), (73.0, 35.0), "ARC", (67.0, 41.0), (-1.0, 41.0)]
SLAB_L1 = [(-1.0, -1.0), (73.0, -1.0), (73.0, 41.0), (-1.0, 41.0)]
#: the level 1 south edge between these Eastings has only a light line
L1_LIGHT_RUN = (30.0, 50.0)

SLAB_OPENINGS_L2 = [
    {"name": "SO1", "box": (30.0, 28.0, 36.0, 32.0), "cross": "solid"},
    {"name": "SO2", "box": (58.0, 8.0, 62.0, 12.0), "cross": "dashed"},
]
#: where SO1 sat in revision 1 (still in the rev 2 file, under a mask)
SO1_OLD_BOX = (20.0, 28.0, 26.0, 32.0)

_T8 = 8.0 / 12.0
_S = 0.375 / math.sqrt(2.0)       # half thickness of the 9 in. angled wall, per axis
WALLS_L2 = [
    {"name": "W1", "mark": "SW1", "poly": [(23.5, 4.0), (24.5, 4.0), (24.5, 16.0), (23.5, 16.0)]},
    {"name": "W2a", "mark": "SW2", "poly": [(28.0, 19.5), (34.0, 19.5), (34.0, 20.5), (28.0, 20.5)]},
    {"name": "W2b", "mark": "SW2", "poly": [(37.0, 19.5), (44.0, 19.5), (44.0, 20.5), (37.0, 20.5)]},
    {"name": "W3", "mark": "SW3", "mark_at": (51.0, 27.4), "poly": [(50.0, 26.0), (60.0, 26.0), (60.0, 36.0),
                                           (60.0 - _T8, 36.0), (60.0 - _T8, 26.0 + _T8),
                                           (50.0, 26.0 + _T8)]},
    {"name": "W4", "mark": "", "clipped": True,
     "poly": [(8.0 + _S, 26.0 - _S), (18.0 + _S, 36.0 - _S), (18.0 - _S, 36.0 + _S),
              (8.0 - _S, 26.0 + _S)]},
    {"name": "W6", "mark": "", "poly": [(30.0, 39.5), (42.0, 39.5), (42.0, 40.5), (30.0, 40.5)]},
]
WALLS_L1 = [w for w in WALLS_L2 if w["name"] in ("W1", "W2a", "W2b")]
#: a wall that exists ONLY in the CAD background (added by an answered RFI)
WALL_CAD_ONLY = {"name": "W5", "poly": [(48.0 - _T8 / 2, 24.0), (48.0 + _T8 / 2, 24.0),
                                        (48.0 + _T8 / 2, 34.0), (48.0 - _T8 / 2, 34.0)]}
COLUMNS = [("A/1", (0.0, 0.0)), ("D/1", (72.0, 0.0)), ("A/3", (0.0, 40.0))]   # 2 ft square
ROUND_COLUMNS = [("C/1", (48.0, 0.0)), ("B/3", (24.0, 40.0)), ("C/3", (48.0, 40.0))]   # 2 ft dia
PAD = [(8.0, 6.0), (13.0, 6.0), (14.0, 8.5), (11.0, 10.5), (8.0, 9.0)]        # other concrete
CORE = {"at": (40.0, 10.0), "radius": 0.25, "text": "6\" DIA CORE"}
MASONRY = [[(2.0, 30.0), (2.0 + _T8, 30.0), (2.0 + _T8, 38.0), (2.0, 38.0)],
           [(10.0, 38.0), (20.0, 38.0), (20.0, 38.0 + _T8), (10.0, 38.0 + _T8)]]

#: deliberate differences in the CAD background (decimal feet)
CAD_SHIFT = {"W1": (0.75 / 12.0, 0.0),          # 3/4 in. east: over the 1/2 in. flag
             "D/1": (0.0, 0.375 / 12.0),        # 3/8 in.: between match and flag
             "C/1": (1.0 / 12.0, 0.0)}          # round column 1 in. off (case 11)
CAD_ORIGIN_FT = (100.0, 50.0)                   # where grid A/1 sits in the CAD file

SCHEDULE = [
    # mark, thickness, min length, end bars, ties, zone length
    ("SW1", "12\"", "10'-0\"", "8-#8", "#4 @ 6\" O.C.", "3'-0\""),
    ("SW2", "12\"", "12'-0\"", "6-#7", "#4 @ 8\" O.C.", "2'-6\""),
    ("SW3", "10\"", "8'-0\"", "6-#6", "#3 @ 8\" O.C.", "2'-0\""),
]
#: the zone length detail disagrees with the schedule for SW2
ZONE_DETAIL = [("SW1", "3'-0\""), ("SW2", "3'-0\""), ("SW3", "2'-0\"")]

NOTES = [
    # column, heading, [(number, text)]
    (0, "GENERAL", [
        ("1", "THE CONTRACTOR SHALL VERIFY ALL DIMENSIONS WITH THE ARCHITECTURAL DRAWINGS BEFORE STARTING WORK."),
        ("2", "DO NOT SCALE THE DRAWINGS."),
    ]),
    (0, "CONCRETE", [
        ("1", "CONCRETE SHALL REACH THE SPECIFIED STRENGTH AT 28 DAYS."),
        ("2", "TOLERANCES FOR CONCRETE CONSTRUCTION SHALL CONFORM TO THE PROJECT SPECIFICATIONS."),
    ]),
    (1, "OPENINGS", [
        ("1", "PROVIDE ADDITIONAL REINFORCING AT ALL SLAB AND WALL OPENINGS PER DETAIL 5/S-501."),
        ("2", "OPENINGS NOT SHOWN ON THE STRUCTURAL DRAWINGS REQUIRE WRITTEN APPROVAL."),
    ]),
    (1, "SURVEY", [
        ("1", "PROVIDE A SLAB ELEVATION SURVEY AFTER EACH ELEVATED SLAB POUR."),
    ]),
]

#: architect dimension strings on A-201: (a, b, printed text, expected class)
ARCH_STRINGS = [
    ((0.0, 10.0), (23.5, 10.0), "23'-6\"", "same"),        # grid A to the west face of SW1
    ((21.0, 0.0), (21.0, 4.0), "4'-0\"", "same"),          # grid 1 to the south end of SW1
    ((19.0, 0.0), (19.0, 16.0), "16'-2\"", "different"),   # grid 1 to the north end: 2 in. off
    ((24.0, 30.0), (30.4, 30.0), "6'-4 3/4\"", "one"),      # grid B to a partition
    ((35.5, 22.5), (48.0, 22.5), "12'-6\"", "one"),        # opening centerline to grid C
    ((10.25, 14.0), (14.75, 14.0), "4'-6\"", "off"),       # partition to partition
]
OVERLAY_OPENINGS = [
    {"name": "OV1", "box": (30.5, 28.5, 35.5, 31.5)},      # inside slab opening SO1: marked
    {"name": "OV2", "box": (40.0, 19.0, 43.0, 21.0)},      # through SW2, inside its end zone
    {"name": "OV3", "box": (10.0, 14.0, 13.0, 17.0)},      # through the slab, not marked
]
RFI_NUMBER = "012"
RFI_ANSWER = ("ADD AN 8 INCH CONCRETE WALL ON GRID C BETWEEN GRIDS 2 AND 3 AS SHOWN "
              "ON THE CAD BACKGROUND.")


def ftin(ft: float) -> str:
    from .model import fmt_ftin
    return fmt_ftin(ft)


class Plan:
    """World feet -> viewer points for one drawn sheet."""

    def __init__(self, ox: float = 150.0, oy: float = 560.0, ppf: float = PPF):
        self.ox, self.oy, self.ppf = ox, oy, ppf

    def P(self, e: float, n: float) -> tuple:
        return (self.ox + e * self.ppf, self.oy - n * self.ppf)

    def poly(self, pts) -> list:
        return [self.P(e, n) for e, n in pts]


def _furniture(s: Sheet, number: str, title: str, rev: str = "",
               scale: str | None = SCALE_NOTE, extra: tuple = ()) -> None:
    """Border, title strip and title-block text."""
    m = 36.0
    s.rect(m, m, s.w - m, s.h - m, width=2.0)
    tx = s.w - m - 150.0
    s.line((tx, m), (tx, s.h - m), width=1.0)
    for y in (s.h - m - 60.0, s.h - m - 110.0, s.h - m - 150.0):
        s.line((tx, y), (s.w - m, y), width=0.5)
    s.text(tx + 8, s.h - m - 122.0, title, 8.5, bold=True)
    if scale:
        s.text(tx + 8, s.h - m - 72.0, "SCALE:", 5.5)
        s.text(tx + 8, s.h - m - 84.0, scale, 8)
    if rev:
        s.text(tx + 96, s.h - m - 72.0, "REVISION", 5.5)
        s.text(tx + 96, s.h - m - 84.0, f"REV {rev}", 8)
    s.text(tx + 8, s.h - m - 50.0, "SHEET", 5.5)
    s.text(tx + 40, s.h - m - 18.0, number, 18, bold=True)
    y = m + 24.0
    for line in extra:
        s.text(tx + 8, y, line, 6.5)
        y += 10.0


def _dim(s: Sheet, a, b, text, off: float = 4.0) -> None:
    """One dimension: its line a-b, slash ticks, the text centered beside
    the middle of the line (rotated for a vertical dimension)."""
    s.line(a, b, width=0.35)
    for p in (a, b):
        s.line((p[0] - 2.5, p[1] + 2.5), (p[0] + 2.5, p[1] - 2.5), width=0.6)
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    w = fitz.get_text_length(text, "helv", 7.0)
    if abs(a[1] - b[1]) <= abs(a[0] - b[0]):
        s.text(mx - w / 2.0, my - off, text, 7.0)
    else:
        s.text(mx - off, my + w / 2.0, text, 7.0, rotate=90)


def _grid(s: Sheet, pl: Plan, dims: bool = True) -> None:
    x0, y0 = pl.P(GRID_X[0][1], GRID_Y[0][1])
    x1, y1 = pl.P(GRID_X[-1][1], GRID_Y[-1][1])
    top, left = y1 - 62.0, x0 - 62.0
    for name, e in GRID_X:
        x = pl.P(e, 0)[0]
        s.line((x, top), (x, y0 + 26.0), width=0.3, dash=[18, 3, 3, 3])
        s.circle((x, top - 10.0), 10.0, width=0.6)
        s.text_c(x, top - 6.8, name, 9, bold=True)
    for name, n in GRID_Y:
        y = pl.P(0, n)[1]
        s.line((left, y), (x1 + 26.0, y), width=0.3, dash=[18, 3, 3, 3])
        s.circle((left - 10.0, y), 10.0, width=0.6)
        s.text_c(left - 10.0, y + 3.2, name, 9, bold=True)
    if dims:
        for (_na, ea), (_nb, eb) in zip(GRID_X, GRID_X[1:]):
            _dim(s, (pl.P(ea, 0)[0], top + 22.0), (pl.P(eb, 0)[0], top + 22.0), ftin(eb - ea))
        for (_na, na), (_nb, nb) in zip(GRID_Y, GRID_Y[1:]):
            _dim(s, (left + 22.0, pl.P(0, na)[1]), (left + 22.0, pl.P(0, nb)[1]), ftin(nb - na))


def _box_poly(box) -> list:
    x0, y0, x1, y1 = box
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _opening(s: Sheet, pl: Plan, box, cross: str, width: float = 0.7) -> None:
    p = pl.poly(_box_poly(box))
    s.polyline(p, width=width, closed=True)
    dash = [4, 3] if cross == "dashed" else None
    s.line(p[0], p[2], width=0.4, dash=dash)
    s.line(p[1], p[3], width=0.4, dash=dash)


def _slab(s: Sheet, pl: Plan, outline, light_run=None, width: float = 1.4) -> None:
    pts = list(outline)
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if a == "ARC":
            continue
        if b == "ARC":
            c = SLAB_ARC["center"]
            # from (73, 35) counter-clockwise (in plan) to (67, 41)
            s.arc(pl.P(*c), SLAB_ARC["radius"] * pl.ppf, 0.0, 90.0, width=width)
            continue
        if light_run and a[1] == b[1] == -1.0 and a[0] < b[0]:
            e0, e1 = light_run
            s.line(pl.P(a[0], a[1]), pl.P(e0, a[1]), width=width)
            s.line(pl.P(e1, a[1]), pl.P(b[0], b[1]), width=width)
            s.line(pl.P(e0, a[1]), pl.P(e1, a[1]), width=0.25)      # only a light line
            continue
        s.line(pl.P(*a), pl.P(*b), width=width)


def _concrete(s: Sheet, pl: Plan, walls, rect_cols=COLUMNS, round_cols=ROUND_COLUMNS,
              pad: bool = True, marks: bool = True) -> None:
    for w in walls:
        p = pl.poly(w["poly"])
        if w.get("clipped"):
            xs, ys = [q[0] for q in p], [q[1] for q in p]
            s.clip_begin(p)
            s.fill_rect(min(xs) - 10, min(ys) - 10, max(xs) + 10, max(ys) + 10, GRAY)
            s.clip_end()
        else:
            s.fill_poly(p, GRAY)
        s.polyline(p, width=0.5, closed=True)
    names = {w["name"]: w for w in walls}
    if "W2a" in names and "W2b" in names:
        # the opening in SW2: header lines dashed across it along both faces
        for n_face in (19.5, 20.5):
            s.line(pl.P(34.0, n_face), pl.P(37.0, n_face), width=0.4, dash=[3, 2])
    for _name, (e, n) in rect_cols:
        p = pl.poly([(e - 1, n - 1), (e + 1, n - 1), (e + 1, n + 1), (e - 1, n + 1)])
        s.fill_poly(p, GRAY)
        s.polyline(p, width=0.5, closed=True)
    for _name, (e, n) in round_cols:
        s.fill_circle(pl.P(e, n), 1.0 * pl.ppf, GRAY)
        s.circle(pl.P(e, n), 1.0 * pl.ppf, width=0.5)
    if pad:
        s.fill_poly(pl.poly(PAD), GRAY)
        s.polyline(pl.poly(PAD), width=0.5, closed=True)
    if marks:
        seen = set()
        for w in walls:
            mk = w.get("mark")
            if not mk or mk in seen:
                continue
            seen.add(mk)
            xs = [q[0] for q in w["poly"]]
            ys = [q[1] for q in w["poly"]]
            if w.get("mark_at"):
                x, y = pl.P(*w["mark_at"])
            elif max(ys) - min(ys) > max(xs) - min(xs):
                x, y = pl.P(max(xs) + 0.6, (min(ys) + max(ys)) / 2.0)
            else:
                x, y = pl.P(min(xs) + 0.6, max(ys) + 0.5)
            s.text(x, y, mk, 7, bold=True)


def sheet_struct(level: int = 2, rev: str = "2", *, old_issue: bool = False,
                 plan: Plan | None = None, hidden_old: bool = True,
                 clouds: bool = True) -> Sheet:
    """The structural slab plan for level 1 or 2.

    Level 2 revision 2 carries the Sightline constructs: the angled wall
    is a big fill limited by a clip path; the slab opening that moved is
    still in the file at its old place under a white mask, with a revision
    cloud beside it; a second cloud crosses the column at A/3.
    ``old_issue=True`` draws revision 1 (the opening at its old place)."""
    pl = plan or Plan()
    s = Sheet(SHEET_W, SHEET_H)
    number = f"S-{level}01"
    _furniture(s, number, f"LEVEL {level} SLAB PLAN", "1" if old_issue else rev)
    _grid(s, pl)
    if level == 2:
        _slab(s, pl, SLAB_L2)
        _concrete(s, pl, WALLS_L2)
        if old_issue:
            _opening(s, pl, SO1_OLD_BOX, "solid")
        else:
            if hidden_old:
                # superseded linework left in the content stream ...
                _opening(s, pl, SO1_OLD_BOX, "solid", width=1.4)
                x0, y1 = pl.P(SO1_OLD_BOX[0] - 0.6, SO1_OLD_BOX[1] - 0.6)
                x1, y0 = pl.P(SO1_OLD_BOX[2] + 0.6, SO1_OLD_BOX[3] + 0.6)
                s.fill_rect(x0, y0, x1, y1, color=1.0)              # ... under a later mask
            _opening(s, pl, SLAB_OPENINGS_L2[0]["box"], "solid")
            if clouds:
                b = SLAB_OPENINGS_L2[0]["box"]
                s.cloud(pl.poly(_box_poly((b[0] - 1.5, b[1] - 1.5, b[2] + 1.5, b[3] + 1.5))))
                tx, ty = pl.P(b[2] + 3.0, b[3] + 2.5)
                s.polyline([(tx, ty - 9), (tx + 8, ty + 5), (tx - 8, ty + 5)], width=0.6,
                           closed=True, color=(0.8, 0, 0))
                s.text_c(tx, ty + 3.0, "2", 6.5, color=(0.8, 0, 0))
                # a cloud drawn across the column at A/3
                cx, cy = pl.P(0.0, 40.0)
                s.cloud([(cx - 24, cy - 2), (cx + 4, cy - 2), (cx + 4, cy + 22), (cx - 24, cy + 22)])
        _opening(s, pl, SLAB_OPENINGS_L2[1]["box"], "dashed")
        c = pl.P(*CORE["at"])
        s.circle(c, CORE["radius"] * pl.ppf, width=0.5)
        s.text(c[0] + 6, c[1] + 2.5, CORE["text"], 6)
        # two wall dimensions (more witnesses for the scale)
        _dim(s, pl.P(22.0, 4.0), pl.P(22.0, 16.0), ftin(12.0))
        _dim(s, pl.P(30.0, 38.2), pl.P(42.0, 38.2), ftin(12.0), off=-9.0)
    else:
        _slab(s, pl, SLAB_L1, light_run=L1_LIGHT_RUN)
        _concrete(s, pl, WALLS_L1, pad=False)
        _dim(s, pl.P(22.0, 4.0), pl.P(22.0, 16.0), ftin(12.0))
    return s


def sheet_arch(plan: Plan | None = None) -> Sheet:
    """A-201: the architectural plan of level 2 (same grid, a different
    place on the sheet) with the dimension strings to check."""
    pl = plan or Plan(ox=190.0, oy=545.0)
    s = Sheet(SHEET_W, SHEET_H)
    _furniture(s, "A-201", "LEVEL 2 FLOOR PLAN", "3")
    _grid(s, pl)
    for w in WALLS_L2:                                   # walls as plain outlines
        s.polyline(pl.poly(w["poly"]), width=0.5, closed=True)
    for a, b, text, _cls in ARCH_STRINGS:
        _dim(s, pl.P(*a), pl.P(*b), text)
    return s


def sheet_notes() -> Sheet:
    """S-001: general notes in two columns, two headings per column."""
    s = Sheet(SHEET_W, SHEET_H)
    _furniture(s, "S-001", "GENERAL NOTES", "2", scale=None)
    s.text(70, 80, "GENERAL NOTES", 14, bold=True)
    col_x = [70.0, 520.0]
    col_y = [120.0, 120.0]
    for col, heading, notes in NOTES:
        x, y = col_x[col], col_y[col]
        s.text(x, y, heading, 9.5, bold=True)
        s.line((x, y + 2.5), (x + fitz.get_text_length(heading, "hebo", 9.5), y + 2.5), width=0.5)
        y += 16.0
        for num, text in notes:
            words = text.split()
            lines, cur = [], ""
            for w in words:
                if fitz.get_text_length((cur + " " + w).strip(), "helv", 7.5) > 380.0:
                    lines.append(cur)
                    cur = w
                else:
                    cur = (cur + " " + w).strip()
            lines.append(cur)
            s.text(x, y, f"{num}.", 7.5)
            for ln in lines:
                s.text(x + 16.0, y, ln, 7.5)
                y += 10.5
            y += 3.0
        col_y[col] = y + 12.0
    return s


def sheet_schedule() -> Sheet:
    """S-501: the shear wall schedule and the boundary zone detail."""
    s = Sheet(SHEET_W, SHEET_H)
    _furniture(s, "S-501", "SHEAR WALL SCHEDULE AND DETAILS", "2", scale=None)
    s.text(70, 90, "SHEAR WALL SCHEDULE", 12, bold=True)
    heads = ["MARK", "THICKNESS", "MIN LENGTH", "END BARS", "TIES", "ZONE LENGTH"]
    xs = [70.0, 130.0, 210.0, 300.0, 380.0, 480.0, 580.0]
    y = 110.0
    rows = [heads] + [list(r) for r in SCHEDULE]
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            s.text(xs[j] + 5, y + 12, cell, 7.5, bold=(i == 0))
        s.line((xs[0], y), (xs[-1], y), width=0.5)
        y += 18.0
    s.line((xs[0], y), (xs[-1], y), width=0.5)
    for x in xs:
        s.line((x, 110.0), (x, y), width=0.5)
    # the detail, with its view title (bubble + name)
    dy = 300.0
    s.rect(70, dy, 330, dy + 110, width=0.5)
    s.fill_rect(90, dy + 40, 300, dy + 58, GRAY)
    s.rect(90, dy + 40, 300, dy + 58, width=0.6)
    s.line((90, dy + 70), (150, dy + 70), width=0.35)
    s.text(96, dy + 82, "ZONE LENGTH", 6.5)
    s.text(350, dy + 20, "BOUNDARY ZONE LENGTH", 8, bold=True)
    yy = dy + 36.0
    for mark, length in ZONE_DETAIL:
        s.text(350, yy, f"{mark} = {length}", 7.5)
        yy += 12.0
    s.circle((84, dy + 136), 11, width=0.8)
    s.text_c(84, dy + 139.5, "5", 10, bold=True)
    s.text(102, dy + 134, "BOUNDARY ZONE DETAIL", 9, bold=True)
    s.line((100, dy + 138), (300, dy + 138), width=0.8)
    s.text(102, dy + 148, "NOT TO SCALE", 6.5)
    return s


def sheet_shop(plan: Plan | None = None) -> Sheet:
    """M-201: a mechanical shop drawing whose openings are overlaid."""
    pl = plan or Plan(ox=170.0, oy=555.0)
    s = Sheet(SHEET_W, SHEET_H)
    _furniture(s, "M-201", "LEVEL 2 MECHANICAL SHOP DRAWING", "1")
    _grid(s, pl)
    for o in OVERLAY_OPENINGS:
        p = pl.poly(_box_poly(o["box"]))
        s.polyline(p, width=0.8, closed=True)
        s.text(p[0][0] + 2, p[2][1] - 4, "DUCT OPENING", 5.5)
    return s


def write_rfi(path: str, number: str = RFI_NUMBER, answer: str = RFI_ANSWER) -> str:
    """An answered RFI response in the plain layout the RFI reader parses."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 72.0
    for line in ["Request for Information",
                 f"Document: {number}",
                 "Title: Wall on grid C at level 2",
                 "Plan Ref: S-201",
                 "Question:",
                 "The CAD background shows a wall on grid C at level 2 that is not on the "
                 "structural plan. Please confirm.",
                 "Answer:", answer]:
        page.insert_text((54, y), line, fontsize=10)
        y += 18.0
    _save(doc, path)
    return path


def cad_shapes(level: int = 2, *, shifts: bool = True, extra_wall: bool = True,
               masonry: bool = True) -> list:
    """The CAD background of a level as ``dxfread.write_background`` shapes,
    in INCHES, with grid A/1 at ``CAD_ORIGIN_FT`` of the file's own origin."""
    ox, oy = CAD_ORIGIN_FT

    def P(e, n, key=None):
        dx, dy = CAD_SHIFT.get(key, (0.0, 0.0)) if shifts else (0.0, 0.0)
        return ((ox + e + dx) * 12.0, (oy + n + dy) * 12.0)

    out = []
    for name, e in GRID_X:
        out.append({"layer": "S-GRID", "kind": "line", "pts": [P(e, -8), P(e, 48)]})
        out.append({"layer": "S-GRID", "kind": "text", "at": P(e, 49.5), "text": name,
                    "height": 12.0})
    for name, n in GRID_Y:
        out.append({"layer": "S-GRID", "kind": "line", "pts": [P(-8, n), P(80, n)]})
        out.append({"layer": "S-GRID", "kind": "text", "at": P(-9.5, n), "text": name,
                    "height": 12.0})
    walls = WALLS_L2 if level == 2 else WALLS_L1
    for w in walls:
        out.append({"layer": "S-WALL", "kind": "poly", "closed": True,
                    "pts": [P(e, n, w["name"]) for e, n in w["poly"]]})
    if extra_wall and level == 2:
        out.append({"layer": "S-WALL", "kind": "poly", "closed": True,
                    "pts": [P(e, n) for e, n in WALL_CAD_ONLY["poly"]]})
    for name, (e, n) in COLUMNS:
        out.append({"layer": "S-COLS", "kind": "poly", "closed": True,
                    "pts": [P(e + a, n + b, name) for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]})
    for name, (e, n) in ROUND_COLUMNS:
        out.append({"layer": "S-COLS", "kind": "circle", "center": P(e, n, name),
                    "radius": 12.0})
    if masonry and level == 2:
        for poly in MASONRY:
            out.append({"layer": "A-WALL-CMU", "kind": "poly", "closed": True,
                        "pts": [P(e, n) for e, n in poly]})
    return out


def build_sample_set(out_dir: str, *, cad: bool = True, arch: bool = True,
                     shop: bool = True, rfi: bool = True, old_issue: bool = True,
                     rot: int = 0) -> dict:
    """Write the whole sample set into ``out_dir``.

    Returns ``{"files": [paths in drop order], "plans": path, ...}``.  The
    plan set PDF holds S-001, S-101, S-201 (rev 2), S-501 and, when asked,
    A-201 and M-201.  ``old_issue`` adds revision 1 of S-201 as its own
    file; ``cad`` adds the level 2 CAD background (ASCII DXF, inches)."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    files = {}
    doc = fitz.open()
    sheet_notes().add_to(doc, rot, True)
    sheet_struct(1).add_to(doc, rot, True)
    sheet_struct(2).add_to(doc, rot, True)
    sheet_schedule().add_to(doc, rot, True)
    if arch:
        sheet_arch().add_to(doc, rot, True)
    if shop:
        sheet_shop().add_to(doc, rot, True)
    files["plans"] = os.path.join(out_dir, "sample_plan_set.pdf")
    _save(doc, files["plans"])
    if old_issue:
        files["old"] = sheet_struct(2, old_issue=True).save(
            os.path.join(out_dir, "S-201_rev1.pdf"), rot, True)
    if cad:
        from .. import dxfread
        files["cad"] = os.path.join(out_dir, "background_L2.dxf")
        dxfread.write_background(files["cad"], cad_shapes(2), units="in")
    if rfi:
        files["rfi"] = write_rfi(os.path.join(out_dir, f"RFI_{RFI_NUMBER}_response.pdf"))
    files["files"] = [files[k] for k in ("plans", "old", "cad", "rfi") if k in files]
    return files
