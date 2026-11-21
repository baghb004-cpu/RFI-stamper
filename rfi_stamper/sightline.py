"""The Sightline: what is really on the plotted sheet.

A PDF page holds vector shapes that are NOT visible on the plotted sheet:
fills limited by a clip path, lines hidden under a later fill or mask, and
superseded revision geometry left in the file.  A reader that trusts raw
vectors draws and measures things that are not on the sheet.  The
Sightline has one job: given a page and its vector elements, return for
each element its VISIBLE part and a visibility score, measured against a
render of the same page.

Why the name: a sightline is what you can actually see from where you
stand.

The rules (owner brief section 4; thresholds are constants here and are
never weakened to make a test pass):

* **Fills.**  Clip paths from the extended drawing data are applied, then
  the result is confirmed against the render.  When the visible area is
  under 95 percent of the vector area the vector shape is replaced by the
  visible polygon (the exact vector clip when the clips are convex, else
  the polygon traced off the render) and both areas are recorded.  An
  angled element is always a polygon, never its bounding box.
* **Strokes.**  Every segment is sampled along its length.  A sample is
  visible when ink of the stroke's own class (its color AND its weight)
  lies within 2 px.  At 80 percent or more visible samples the segment is
  visible; under 50 percent it is hidden, dropped and recorded; in between
  it is split where visibility changes.
* **Annotations over geometry.**  A revision cloud, tag or leader drawn
  across a fill is thin ink over a shape that is still there: the element
  is classified OBSCURED and listed separately, never reported missing.
* **Reverse check.**  Pixels of a tracked fill class that no accepted
  element covers come back as regions ("seen but not read").
* **Honest limits.**  What cannot be judged (ink the color of the paper,
  a stroke inside a region of its own color, a transparent fill that does
  not match its blend) is kept and recorded as unreadable, never dropped.
  If the vectors do not register to the render at all the whole page is
  recorded unreadable and nothing is dropped.

Renders are 150 dpi minimum; elements too small to judge there are judged
on 300 dpi tiles.  The gate saves per page what it dropped, what it
clipped and what it could not read (:class:`PageRecord`).

Coordinates: results are in VIEWER page points (top-left origin, y down,
the page as rendered: ``get_drawings`` coordinates pushed through
``page.rotation_matrix``).  :meth:`Result.drawings` converts back to the
raw ``get_drawings`` shape for the existing readers.

GUI-free.  fitz + numpy only.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

import fitz
import numpy as np

from . import geom2d as g2

DPI = 150                 # base render (the brief's minimum)
DPI_SMALL = 300           # small elements are judged here
FILL_KEEP = 0.95          # visible area / vector area below this: replace the shape
FILL_GONE = 0.05          # below this the fill is hidden outright
STROKE_VISIBLE = 0.80     # visible samples at or above this: visible
STROKE_HIDDEN = 0.50      # below this: hidden, dropped, recorded
REACH_PX = 2              # own-class ink must lie within this many px
STEP_PX = 2.0             # sample spacing along a stroke
MAX_SAMPLES = 4096        # per segment
SMALL_PX = 8.0            # shorter strokes / thinner fills go to the 300 dpi pass
HAIR_PX = 0.2             # rendered ink across the thinnest line (measured)
INK_SHARE = 0.5           # a sample needs this share of its expected ink
COLOR_TOL = 40.0          # RGB distance (0..441) that still counts as the fill's class
FAINT = 30.0              # ink closer than this to the paper cannot be judged
FLAT = 0.15               # local contrast range below this: the surroundings already have the ink color
THIN_PX = 2               # overlay ink thinner than (2*THIN_PX + 1) px is an annotation, not a mask
OBSCURED_MIN = 0.01       # share of a fill under annotation ink before it is listed obscured
REGISTER_MIN = 0.40       # under this share of stroke length found on the render: registration failed
REGISTER_STROKES = 40     # ... judged only when the page has at least this many strokes
TILE_PT = 480.0           # small-pass tile size in viewer points
PAPER = (255, 255, 255)

VISIBLE, CLIPPED, SPLIT, HIDDEN, UNREADABLE, MASK = (
    "visible", "clipped", "split", "hidden", "unreadable", "mask")

_ENABLED = os.environ.get("PLOOM_SIGHTLINE", "on").lower() not in ("off", "0", "no")


def enabled() -> bool:
    """The default-on switch for the retrofitted readers.  Set
    ``PLOOM_SIGHTLINE=off`` to read raw vectors (the pre-Sightline
    behaviour, kept as a parity path)."""
    return _ENABLED


def set_enabled(on: bool) -> None:
    global _ENABLED
    _ENABLED = bool(on)


# ---------------------------------------------------------------- records --

@dataclass
class Fill:
    """One filled path after the gate (viewer points)."""
    seq: int                      # index of the source path in Result.paths
    rgb: tuple                    # fill color, 0..255
    polys: list                   # VISIBLE polygons
    status: str = VISIBLE
    method: str = "vector"        # "vector" | "traced"
    area_vector: float = 0.0      # pt^2, as drawn in the file
    area_visible: float = 0.0     # pt^2, measured on the render
    score: float = 1.0
    obscured: float = 0.0         # share under thin annotation ink
    curved: bool = False          # the outline has curve segments
    vector_polys: list = field(default_factory=list)   # the outline as drawn
    why: str = ""

    def bbox(self) -> tuple:
        pts = [p for poly in (self.polys or self.vector_polys) for p in poly]
        return g2.bbox(pts) if pts else (0.0, 0.0, 0.0, 0.0)


@dataclass
class Stroke:
    """One stroked item (a line, a rectangle edge or a curve) after the
    gate (viewer points)."""
    seq: int                      # index of the source path in Result.paths
    item: int                     # index of the item inside that path
    rgb: tuple
    width: float                  # points
    dashes: str
    pts: list                     # the VISIBLE polyline (2 points for a line)
    status: str = VISIBLE
    score: float = 1.0
    curve: tuple | None = None    # the four Bezier control points when the item is a curve
    part: int = 0                 # 0.. for the pieces of a split item
    why: str = ""

    @property
    def length(self) -> float:
        return sum(math.hypot(self.pts[i + 1][0] - self.pts[i][0],
                              self.pts[i + 1][1] - self.pts[i][1])
                   for i in range(len(self.pts) - 1))


@dataclass
class PageRecord:
    """What the gate did on one page; the run report prints these lists."""
    page: int = 0
    dpi: int = DPI
    judged: int = 0
    dropped: list = field(default_factory=list)      # hidden elements
    clipped: list = field(default_factory=list)      # fills replaced / strokes split
    obscured: list = field(default_factory=list)     # annotation over geometry
    unreadable: list = field(default_factory=list)   # could not be judged (kept)
    unread_regions: list = field(default_factory=list)   # reverse check
    notes: list = field(default_factory=list)

    def counts(self) -> dict:
        return {"judged": self.judged, "dropped": len(self.dropped),
                "clipped": len(self.clipped), "obscured": len(self.obscured),
                "unreadable": len(self.unreadable),
                "unread_regions": len(self.unread_regions)}

    def to_dict(self) -> dict:
        return {"page": self.page, "dpi": self.dpi, "judged": self.judged,
                "dropped": self.dropped, "clipped": self.clipped,
                "obscured": self.obscured, "unreadable": self.unreadable,
                "unread_regions": self.unread_regions, "notes": self.notes}


def _entry(what, bbox, **kw) -> dict:
    d = {"what": what, "bbox": [round(float(v), 2) for v in bbox]}
    d.update(kw)
    return d


# ----------------------------------------------------------------- render --

class Render:
    """A page render plus the map from viewer points to its pixels."""

    def __init__(self, page, dpi: int = DPI, clip=None):
        self.dpi = int(dpi)
        self.zoom = self.dpi / 72.0
        kw = {"dpi": self.dpi, "colorspace": fitz.csRGB, "alpha": False}
        if clip is not None:
            r = fitz.Rect(clip) & page.rect
            kw["clip"] = r
        pix = page.get_pixmap(**kw)
        self.rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)[:, :, :3]
        self.h, self.w = self.rgb.shape[:2]
        # pixel (0, 0) of a clipped pixmap sits at pix.x, pix.y in the
        # full-page pixel grid
        self.ox = pix.x / self.zoom
        self.oy = pix.y / self.zoom
        self._f32 = None
        self._ink: dict = {}
        self._sim: dict = {}
        self._gray = None
        self._col_ix = None

    @property
    def f32(self) -> np.ndarray:
        if self._f32 is None:
            self._f32 = self.rgb.astype(np.float32)
        return self._f32

    def px(self, x, y):
        """Viewer points -> continuous pixel coordinates."""
        return ((np.asarray(x, dtype=float) - self.ox) * self.zoom,
                (np.asarray(y, dtype=float) - self.oy) * self.zoom)

    def _split(self) -> None:
        """Drawings are almost all gray ink on white: keep one gray channel
        and the (few) colored pixel positions, so a gray ink's distance
        image is a table lookup instead of a 3-channel square root."""
        if self._gray is None:
            r = self.rgb[..., 0].astype(np.int16)
            g = self.rgb[..., 1].astype(np.int16)
            b = self.rgb[..., 2].astype(np.int16)
            colored = (np.abs(r - g) > 2) | (np.abs(g - b) > 2)
            self._gray = self.rgb[..., 1]
            self._col_ix = np.nonzero(colored)

    def dist(self, rgb) -> np.ndarray:
        """RGB distance (0..441) of every pixel to a color, float32."""
        c = tuple(int(v) for v in rgb)
        self._split()
        rows, cols = self._col_ix
        if c[0] == c[1] == c[2] and len(rows) * 4 < self.w * self.h:
            lut = (np.abs(np.arange(256, dtype=np.float32) - c[0])
                   * np.float32(math.sqrt(3.0)))
            d = lut[self._gray]
            if len(rows):
                px = self.rgb[rows, cols].astype(np.float32) - np.asarray(c, np.float32)
                d[rows, cols] = np.sqrt((px * px).sum(axis=1))
            return d
        dd = self.f32 - np.asarray(c, dtype=np.float32)
        return np.sqrt((dd * dd).sum(axis=2))

    def class_mask(self, rgb, tol: float = COLOR_TOL) -> np.ndarray:
        """Pixels of a fill class (within ``tol`` of its color)."""
        return self.dist(rgb) <= tol

    def patch_match(self, rgb, r0, r1, c0, c1, tol: float = COLOR_TOL) -> np.ndarray:
        """:meth:`class_mask` over a pixel window only."""
        px = self.rgb[r0:r1, c0:c1].astype(np.float32) - np.asarray(rgb, np.float32)
        return np.sqrt((px * px).sum(axis=2)) <= tol

    def ink(self, rgb, r_bg: int):
        """Local-contrast ink of a stroke class: 1 where the pixel has the
        stroke's color and its surroundings do not, 0 where it matches its
        surroundings.  Returns ``(ink, flat)``; ``flat`` marks pixels whose
        whole neighbourhood already has the stroke's color (a stroke there
        cannot be judged).  ``None`` when the color is too close to the
        paper."""
        key = (tuple(int(v) for v in rgb), int(r_bg))
        if key in self._ink:
            return self._ink[key]
        c = np.asarray(rgb, dtype=np.float32)
        d_bg = float(np.sqrt(((np.asarray(PAPER, dtype=np.float32) - c) ** 2).sum()))
        if d_bg < FAINT:
            self._ink[key] = None
            return None
        sim = self._sim.get(key[0])
        if sim is None:
            sim = self.dist(rgb)
            sim *= np.float32(-1.0 / d_bg)
            sim += np.float32(1.0)
            np.clip(sim, 0.0, 1.0, out=sim)
            self._sim[key[0]] = sim
        lo = g2.min_filter(sim, r_bg)
        flat = lo >= np.float32(1.0 - FLAT)
        ink = sim - lo
        rng = np.float32(1.0) - lo
        np.maximum(rng, np.float32(1e-6), out=rng)
        ink /= rng
        ink[flat] = 0.0
        self._ink[key] = (ink, flat)
        return self._ink[key]


_RENDERS: dict = {}
_RENDERS_MAX = 3


def render_cached(page, dpi: int = DPI) -> "Render":
    """A base :class:`Render` of the page, reused across gate calls (the
    ink images ride on it)."""
    key = _page_key(page, dpi)
    if key is None:
        return Render(page, dpi)
    rd = _RENDERS.get(key)
    if rd is None:
        if len(_RENDERS) >= _RENDERS_MAX:
            _RENDERS.pop(next(iter(_RENDERS)))
        rd = _RENDERS[key] = Render(page, dpi)
    return rd


def _page_key(page, dpi):
    doc = page.parent
    try:
        sig = b"".join(doc.xref_stream_raw(x) or b"" for x in page.get_contents())
    except Exception:   # noqa: BLE001 -- unreadable stream: do not cache
        return None
    try:
        annots = tuple(x[0] for x in page.annot_xrefs())
    except Exception:   # noqa: BLE001
        annots = ()
    return (id(doc), doc.name, page.number, page.rotation, hash(sig), annots,
            int(dpi))


def rgb8(c) -> tuple | None:
    """A fitz color (floats 0..1) -> an (r, g, b) 0..255 key."""
    if c is None:
        return None
    try:
        if len(c) == 1:
            c = (c[0],) * 3
        k = [int(round(max(0.0, min(1.0, float(v))) * 255.0)) for v in c[:3]]
        if max(k) - min(k) <= 2:
            # the renderer reports grays with channel noise in the last
            # place: snap them to one true gray so equal inks key equally
            g = int(round(sum(max(0.0, min(1.0, float(v))) for v in c[:3]) / 3.0 * 255.0))
            k = [g, g, g]
        return tuple(k)
    except TypeError:
        v = int(round(max(0.0, min(1.0, float(c))) * 255.0))
        return (v, v, v)


def _blend(rgb, opacity) -> tuple:
    if opacity is None or opacity >= 0.999:
        return rgb
    return tuple(int(round(v * opacity + 255 * (1.0 - opacity))) for v in rgb)


def _dash_period(dashes) -> float:
    """Dash period in points (0 = solid)."""
    if not dashes:
        return 0.0
    m = re.match(r"\s*\[([^\]]*)\]", str(dashes))
    if not m:
        return 0.0
    nums = [float(v) for v in m.group(1).split() if _isnum(v)]
    if not nums or sum(nums) <= 0:
        return 0.0
    if len(nums) % 2:
        nums = nums * 2
    return float(sum(nums))


def _isnum(s) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# --------------------------------------------------------- path geometry --

class _Xf:
    """get_drawings coordinates -> viewer points (and back)."""

    def __init__(self, page):
        m = page.rotation_matrix
        self.m = (m.a, m.b, m.c, m.d, m.e, m.f)
        det = m.a * m.d - m.b * m.c
        self.inv = (m.d / det, -m.b / det, -m.c / det, m.a / det,
                    (m.c * m.f - m.d * m.e) / det, (m.b * m.e - m.a * m.f) / det)

    def fwd(self, p) -> tuple:
        a, b, c, d, e, f = self.m
        return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)

    def back(self, p) -> tuple:
        a, b, c, d, e, f = self.inv
        return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def _item_pieces(item, xf: _Xf) -> list:
    """One drawing item -> stroke pieces in viewer points:
    ``[("l", [p, q]) | ("c", [p0, c1, c2, p1])]``."""
    op = item[0]
    if op == "l":
        return [("l", [xf.fwd(item[1]), xf.fwd(item[2])])]
    if op == "c":
        return [("c", [xf.fwd(p) for p in item[1:5]])]
    if op == "re":
        r = item[1]
        c = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
        c = [xf.fwd(p) for p in c]
        return [("l", [c[i], c[(i + 1) % 4]]) for i in range(4)]
    if op == "qu":
        q = item[1]
        c = [xf.fwd(p) for p in (q.ul, q.ur, q.lr, q.ll)]
        return [("l", [c[i], c[(i + 1) % 4]]) for i in range(4)]
    return []


def _subpaths(items, xf: _Xf) -> tuple:
    """Path items -> closed outline polygons in viewer points.
    Returns ``(polys, curved)``."""
    polys: list = []
    cur: list = []
    curved = False

    def flush():
        nonlocal cur
        if len(cur) >= 3:
            p = g2.dedupe(cur, 1e-6)
            if len(p) >= 3:
                polys.append(p)
        cur = []

    for it in items:
        op = it[0]
        if op in ("re", "qu"):
            flush()
            if op == "re":
                r = it[1]
                c = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
            else:
                q = it[1]
                c = [q.ul, q.ur, q.lr, q.ll]
            polys.append([xf.fwd(p) for p in c])
            continue
        if op == "l":
            a, b = xf.fwd(it[1]), xf.fwd(it[2])
            seg = [a, b]
        elif op == "c":
            curved = True
            seg = g2.bezier(*[xf.fwd(p) for p in it[1:5]], n=12)
        else:
            continue
        if cur and math.hypot(cur[-1][0] - seg[0][0], cur[-1][1] - seg[0][1]) > 1e-3:
            flush()
        if not cur:
            cur.append(seg[0])
        cur.extend(seg[1:])
    flush()
    return polys, curved


def _clip_stacks(ext) -> list:
    """Walk ``get_drawings(extended=True)``: -> ``[(path, [clip, ...])]``
    for every drawn path, with the clip entries that enclose it."""
    out = []
    stack: list = []
    for d in ext:
        lvl = d.get("level", 0) or 0
        while stack and (stack[-1].get("level", 0) or 0) >= lvl:
            stack.pop()
        t = d.get("type")
        if t == "clip":
            stack.append(d)
        elif t == "group":
            continue
        else:
            out.append((d, list(stack)))
    return out


# ------------------------------------------------------------ the result --

class Result:
    """Everything the gate found on one page."""

    def __init__(self, page_no: int, dpi: int):
        self.page_no = page_no
        self.dpi = dpi
        self.paths: list = []          # the drawn path dicts, in file order
        self.fills: list = []          # visible + clipped Fill records
        self.strokes: list = []        # visible + split Stroke records
        self.hidden_fills: list = []
        self.hidden_strokes: list = []
        self.record = PageRecord(page=page_no, dpi=dpi)
        self.render: Render | None = None
        self.registered = True
        self._xf: _Xf | None = None
        self._stroke_out: dict = {}    # (seq, item) -> [pieces] | None (= untouched)
        self._fill_out: dict = {}      # seq -> [polys] | "drop" | None

    # -- the drop-in for existing readers --------------------------------- #
    def drawings(self) -> list:
        """The page's drawings in the raw ``get_drawings()`` shape with
        hidden items removed, split strokes replaced by their visible
        pieces and clipped fills replaced by their visible outline.
        Untouched paths come back equal to the raw ones."""
        xf = self._xf
        out = []

        def plain(d, items=None):
            nd = {k: v for k, v in d.items() if k != "level"}
            if items is not None:
                nd["items"] = items
                pts = [p for it in items for p in it[1:] if isinstance(p, fitz.Point)]
                if pts:
                    nd["rect"] = fitz.Rect(min(p.x for p in pts), min(p.y for p in pts),
                                           max(p.x for p in pts), max(p.y for p in pts))
            return nd

        for seq, d in enumerate(self.paths):
            fill_mod = self._fill_out.get(seq)
            is_fill = d.get("type") in ("f", "fs")
            is_stroke = d.get("type") in ("s", "fs")
            if is_fill and not is_stroke:
                if fill_mod in ("drop", "mask"):
                    continue
                out.append(plain(d) if fill_mod is None
                           else plain(d, _poly_items(fill_mod, xf)))
                continue
            items = []
            changed = False
            for k, it in enumerate(d.get("items") or ()):
                mod = self._stroke_out.get((seq, k))
                if mod is None:
                    items.append(it)
                    continue
                changed = True
                for kind, pts in mod:
                    if kind == "l":
                        items.append(("l", fitz.Point(xf.back(pts[0])),
                                      fitz.Point(xf.back(pts[1]))))
                    else:
                        items.append(("c",) + tuple(fitz.Point(xf.back(p)) for p in pts))
            if is_fill and fill_mod not in (None, "drop", "mask") and not changed:
                out.append(plain(d, _poly_items(fill_mod, xf)))
                continue
            if not items:
                continue                      # every stroked item is hidden
            out.append(plain(d, items) if changed else plain(d))
        return out

    def fill_classes(self) -> list:
        """Fill colors on the page by visible area:
        ``[{"rgb", "count", "area"}]``, largest first."""
        acc: dict = {}
        for f in self.fills:
            a = acc.setdefault(f.rgb, {"rgb": f.rgb, "count": 0, "area": 0.0})
            a["count"] += 1
            a["area"] += f.area_visible
        return sorted(acc.values(), key=lambda a: -a["area"])


def _poly_items(polys, xf: _Xf) -> list:
    items = []
    for poly in polys:
        n = len(poly)
        for i in range(n):
            items.append(("l", fitz.Point(xf.back(poly[i])),
                          fitz.Point(xf.back(poly[(i + 1) % n]))))
    return items


# ------------------------------------------------------------- the gate --

def gate(page, drawings=None, *, dpi: int = DPI, min_len_pt: float = 0.0,
         small: bool = True) -> Result:
    """Run the visibility gate over one page.

    ``drawings`` may carry a pre-fetched ``page.get_drawings(extended=
    True)`` list.  ``min_len_pt`` lets a reader that ignores short strokes
    say so: strokes under it are passed through unjudged (and unrecorded)
    instead of triggering the 300 dpi pass.  ``small=False`` skips the
    300 dpi pass (small elements are then recorded unreadable)."""
    dpi = max(int(dpi), DPI)
    ext = page.get_drawings(extended=True) if drawings is None else drawings
    res = Result(page.number + 1, dpi)
    xf = res._xf = _Xf(page)
    stacks = _clip_stacks(ext)
    res.paths = [d for d, _c in stacks]
    rd = res.render = render_cached(page, dpi)

    # ---- collect ------------------------------------------------------- #
    s_meta: list = []        # (seq, item, piece_no, kind, pts, rgb, width, dashes, in_fs)
    f_meta: list = []        # (seq, d, polys, curved, clip_geo, rgb, opacity)
    paper_strokes = 0
    clip_cache: dict = {}

    def clip_geo_of(clips):
        geo = []
        for cl in clips:
            got = clip_cache.get(id(cl))
            if got is None:
                got = clip_cache[id(cl)] = (
                    _subpaths(cl.get("items") or (), xf)[0], bool(cl.get("even_odd")))
            geo.append(got)
        return geo

    for seq, (d, clips) in enumerate(stacks):
        t = d.get("type")
        if t in ("s", "fs") and d.get("color") is not None:
            rgb = _blend(rgb8(d.get("color")), d.get("stroke_opacity"))
            width = float(d.get("width") or 0.0)
            for k, it in enumerate(d.get("items") or ()):
                for pn, (kind, pts) in enumerate(_item_pieces(it, xf)):
                    s_meta.append((seq, k, pn, kind, pts, rgb, width,
                                   d.get("dashes") or "", t == "fs"))
        if t in ("f", "fs") and d.get("fill") is not None:
            polys, curved = _subpaths(d.get("items") or (), xf)
            if polys:
                f_meta.append((seq, d, polys, curved, clip_geo_of(clips),
                               rgb8(d.get("fill")), d.get("fill_opacity")))

    # ---- strokes ------------------------------------------------------- #
    judged = _judge_strokes(rd, s_meta, min_len_pt)
    small_s = [i for i, j in enumerate(judged) if j is not None and j["status"] == "small"]
    if small_s and small:
        _small_pass_strokes(page, s_meta, judged, small_s)
    tot_len = vis_len = 0.0
    n_judged = 0
    for i, j in enumerate(judged):
        if j is None or j["status"] in ("small", "skip", UNREADABLE, MASK):
            continue
        ln = j["len"]
        tot_len += ln
        vis_len += ln * j["score"]
        n_judged += 1
    if n_judged >= REGISTER_STROKES and tot_len > 0 and vis_len / tot_len < REGISTER_MIN:
        res.registered = False
        res.record.notes.append(
            f"only {100.0 * vis_len / tot_len:.0f}% of the stroke length in the "
            "file was found on the render: the vectors do not register to the "
            "plotted sheet, so nothing on this page was judged or dropped")
        res.record.unreadable.append(_entry(
            "page", (0, 0, page.rect.width, page.rect.height),
            why="vectors do not register to the render"))
        _passthrough(res, s_meta, f_meta)
        return res

    per_item: dict = {}
    for i, j in enumerate(judged):
        seq, k, pn, kind, pts, rgb, width, dashes, _fs = s_meta[i]
        per_item.setdefault((seq, k), []).append((i, j))
        if j is None or j["status"] == "skip":
            st = Stroke(seq, k, rgb, width, dashes, list(pts) if kind == "l"
                        else g2.bezier(*pts, n=12), VISIBLE, 1.0,
                        tuple(pts) if kind == "c" else None, why="not judged")
            res.strokes.append(st)
            continue
        if j["status"] == MASK:
            paper_strokes += 1
            res.strokes.append(Stroke(seq, k, rgb, width, dashes,
                                      list(pts) if kind == "l" else g2.bezier(*pts, n=12),
                                      MASK, 1.0, tuple(pts) if kind == "c" else None,
                                      why="drawn in the paper color"))
            continue
        res.record.judged += 1
        full = list(pts) if kind == "l" else g2.bezier(*pts, n=12)
        bb = g2.bbox(full)
        if j["status"] in (UNREADABLE, "small"):
            why = j.get("why") or "too small to judge at this resolution"
            res.record.unreadable.append(_entry("stroke", bb, why=why))
            res.strokes.append(Stroke(seq, k, rgb, width, dashes, full, UNREADABLE,
                                      j["score"], tuple(pts) if kind == "c" else None,
                                      why=why))
        elif j["status"] == VISIBLE:
            res.strokes.append(Stroke(seq, k, rgb, width, dashes, full, VISIBLE,
                                      j["score"], tuple(pts) if kind == "c" else None))
        elif j["status"] == HIDDEN:
            st = Stroke(seq, k, rgb, width, dashes, full, HIDDEN, j["score"],
                        tuple(pts) if kind == "c" else None,
                        why="not on the plotted sheet")
            res.hidden_strokes.append(st)
            res.record.dropped.append(_entry(
                "stroke", bb, score=round(j["score"], 3), width=round(width, 3),
                rgb=list(rgb), length=round(st.length, 2),
                why="hidden: under 50% of its length is on the plotted sheet"))
        else:                                               # SPLIT
            parts = []
            for a, b in j["runs"]:
                if kind == "l":
                    p0 = (pts[0][0] + (pts[1][0] - pts[0][0]) * a,
                          pts[0][1] + (pts[1][1] - pts[0][1]) * a)
                    p1 = (pts[0][0] + (pts[1][0] - pts[0][0]) * b,
                          pts[0][1] + (pts[1][1] - pts[0][1]) * b)
                    parts.append(("l", [p0, p1]))
                else:
                    parts.append(("c", list(g2.bezier_split(*pts, a, b))))
            for n_part, (pk, pp) in enumerate(parts):
                res.strokes.append(Stroke(
                    seq, k, rgb, width, dashes,
                    list(pp) if pk == "l" else g2.bezier(*pp, n=12), SPLIT,
                    j["score"], tuple(pp) if pk == "c" else None, part=n_part,
                    why="split where visibility changes"))
            res.record.clipped.append(_entry(
                "stroke", bb, score=round(j["score"], 3), pieces=len(parts),
                why="split: partly hidden on the plotted sheet"))
            j["parts"] = parts
    if paper_strokes:
        res.record.notes.append(
            f"{paper_strokes} stroke piece(s) drawn in the paper color were not judged")

    # stroke replacements for the drop-in
    for (seq, k), lst in per_item.items():
        if all(j is None or j["status"] in (VISIBLE, "skip", UNREADABLE, "small", MASK)
               for _i, j in lst):
            continue
        pieces = []
        for i, j in lst:
            kind, pts = s_meta[i][3], s_meta[i][4]
            if j is None or j["status"] in (VISIBLE, "skip", UNREADABLE, "small", MASK):
                pieces.append((kind, list(pts)))
            elif j["status"] == SPLIT:
                pieces.extend(j.get("parts", []))
        res._stroke_out[(seq, k)] = pieces

    # ---- fills --------------------------------------------------------- #
    small_f = []
    for n, fm in enumerate(f_meta):
        out = _judge_fill(rd, fm)
        if out["status"] == "small":
            small_f.append((n, fm))
            continue
        _file_fill(res, fm, out)
    if small_f:
        if small:
            _small_pass_fills(page, res, small_f)
        else:
            for _n, fm in small_f:
                _file_fill(res, fm, {"status": UNREADABLE, "polys": fm[2],
                                     "why": "too small to judge at this resolution",
                                     "area_vector": _poly_area(fm[2]),
                                     "area_visible": _poly_area(fm[2]),
                                     "score": 1.0, "obscured": 0.0, "method": "vector"})
    return res


def _poly_area(polys) -> float:
    return float(sum(abs(g2.area(p)) for p in polys))


def _passthrough(res: Result, s_meta, f_meta) -> None:
    """Registration failed: hand everything back unjudged."""
    res.strokes, res.fills = [], []
    res.hidden_strokes, res.hidden_fills = [], []
    res._stroke_out, res._fill_out = {}, {}
    res.record.dropped, res.record.clipped, res.record.obscured = [], [], []
    for seq, k, _pn, kind, pts, rgb, width, dashes, _fs in s_meta:
        res.strokes.append(Stroke(seq, k, rgb, width, dashes,
                                  list(pts) if kind == "l" else g2.bezier(*pts, n=12),
                                  UNREADABLE, 0.0, tuple(pts) if kind == "c" else None,
                                  why="page did not register"))
    for seq, _d, polys, curved, _clips, rgb, _op in f_meta:
        a = _poly_area(polys)
        res.fills.append(Fill(seq, rgb, polys, UNREADABLE, "vector", a, a, 0.0,
                              0.0, curved, polys, "page did not register"))


def _file_fill(res: Result, fm, out) -> None:
    seq, _d, polys, curved, _clips, rgb, _op = fm
    st = out["status"]
    f = Fill(seq, rgb, out.get("polys", polys), st, out.get("method", "vector"),
             out.get("area_vector", 0.0), out.get("area_visible", 0.0),
             out.get("score", 1.0), out.get("obscured", 0.0), curved, polys,
             out.get("why", ""))
    bb = g2.bbox([p for poly in polys for p in poly])
    if st == MASK:
        # a fill in the paper color is a mask, not linework: it hides what
        # is under it (the render already shows that) and its own outline
        # is not on the plotted sheet, so line readers never see it
        res.fills.append(f)
        res._fill_out[seq] = "mask"
        return
    res.record.judged += 1
    if st == HIDDEN:
        res.hidden_fills.append(f)
        res._fill_out[seq] = "drop"
        res.record.dropped.append(_entry(
            "fill", bb, rgb=list(rgb), area_vector=round(f.area_vector, 2),
            area_visible=round(f.area_visible, 2), why=f.why or "hidden"))
        return
    if st == UNREADABLE:
        res.record.unreadable.append(_entry("fill", bb, rgb=list(rgb), why=f.why))
    elif st == CLIPPED:
        res._fill_out[seq] = f.polys
        res.record.clipped.append(_entry(
            "fill", bb, rgb=list(rgb), area_vector=round(f.area_vector, 2),
            area_visible=round(f.area_visible, 2), method=f.method, why=f.why))
    if f.obscured >= OBSCURED_MIN and st in (VISIBLE, CLIPPED):
        res.record.obscured.append(_entry(
            "fill", bb, rgb=list(rgb), share=round(f.obscured, 3),
            why="annotation ink drawn across the element; the element is still there"))
    res.fills.append(f)


# ---------------------------------------------------------------- strokes --

def _judge_strokes(rd: Render, s_meta, min_len_pt: float, small_pass: bool = False,
                   only=None) -> list:
    """Sample every stroke piece against the render.  Returns one dict per
    piece: ``{"status", "score", "len", "runs"}`` (or None = not judged)."""
    n = len(s_meta)
    out: list = [None] * n
    todo = range(n) if only is None else only
    groups: dict = {}
    for i in todo:
        seq, k, pn, kind, pts, rgb, width, dashes, _fs = s_meta[i]
        poly = list(pts) if kind == "l" else g2.bezier(*pts, n=16)
        ln = sum(math.hypot(poly[q + 1][0] - poly[q][0], poly[q + 1][1] - poly[q][1])
                 for q in range(len(poly) - 1))
        ln_px = ln * rd.zoom
        if ln <= 1e-6:
            out[i] = {"status": "skip", "score": 1.0, "len": 0.0}
            continue
        if ln < min_len_pt:
            out[i] = {"status": "skip", "score": 1.0, "len": ln}
            continue
        if math.dist(rgb, PAPER) < FAINT:
            out[i] = {"status": MASK, "score": 1.0, "len": ln}
            continue
        if ln_px < SMALL_PX and not small_pass:
            out[i] = {"status": "small", "score": 1.0, "len": ln}
            continue
        w_px = width * rd.zoom
        half = int(math.ceil(w_px / 2.0)) + REACH_PX
        groups.setdefault((rgb, half), []).append((i, poly, ln_px, w_px,
                                                   _dash_period(dashes) * rd.zoom))
    r_for: dict = {}
    for (rgb, half) in groups:
        if half <= 8:
            r_for[rgb] = max(r_for.get(rgb, 0), half + 5)
    for (rgb, half), members in groups.items():
        r_bg = r_for[rgb] if half <= 8 else half + 5
        got = rd.ink(rgb, r_bg)
        if got is None:
            for i, *_ in members:
                out[i] = {"status": MASK, "score": 1.0, "len": s_len(s_meta[i])}
            continue
        ink, flat = got
        # ---- build the sample arrays for the whole group ------------------ #
        xs_l, ys_l, nx_l, ny_l, owner = [], [], [], [], []
        counts = []
        for m, (i, poly, ln_px, w_px, _dash) in enumerate(members):
            ns = int(min(MAX_SAMPLES, max(2, math.ceil(ln_px / STEP_PX))))
            counts.append(ns)
            px, py, tx, ty = _along(poly, ns)
            ax, ay = rd.px(px, py)
            xs_l.append(ax)
            ys_l.append(ay)
            nx_l.append(-ty)
            ny_l.append(tx)
        xs = np.concatenate(xs_l)
        ys = np.concatenate(ys_l)
        nx = np.concatenate(nx_l)
        ny = np.concatenate(ny_l)
        offs = np.arange(-half, half + 1, dtype=np.float32)
        sx = np.rint(xs[:, None] + nx[:, None] * offs[None, :] - 0.5).astype(np.int64)
        sy = np.rint(ys[:, None] + ny[:, None] * offs[None, :] - 0.5).astype(np.int64)
        inside = (sx >= 0) & (sx < rd.w) & (sy >= 0) & (sy < rd.h)
        sxc = np.clip(sx, 0, rd.w - 1)
        syc = np.clip(sy, 0, rd.h - 1)
        prof = np.where(inside, ink[syc, sxc], 0.0).sum(axis=1)
        cx = np.clip(np.rint(xs - 0.5).astype(np.int64), 0, rd.w - 1)
        cy = np.clip(np.rint(ys - 0.5).astype(np.int64), 0, rd.h - 1)
        on_page = (xs >= 0) & (xs < rd.w) & (ys >= 0) & (ys < rd.h)
        unread = flat[cy, cx] & on_page
        pos = 0
        for m, (i, poly, ln_px, w_px, dash_px) in enumerate(members):
            ns = counts[m]
            sl = slice(pos, pos + ns)
            pos += ns
            need = INK_SHARE * max(w_px, HAIR_PX)
            vis = prof[sl] >= need
            ur = unread[sl]
            if dash_px > 0:
                k = int(math.ceil(dash_px / (ln_px / ns))) + 1
                vis = _spread(vis, k)
            readable = ~ur
            n_read = int(readable.sum())
            ln = ln_px / rd.zoom
            if n_read < 0.5 * ns:
                out[i] = {"status": UNREADABLE, "score": 1.0, "len": ln,
                          "why": "inside a region of its own color; cannot be judged"}
                continue
            score = float((vis & readable).sum()) / n_read
            if score >= STROKE_VISIBLE:
                out[i] = {"status": VISIBLE, "score": score, "len": ln}
            elif score < STROKE_HIDDEN:
                out[i] = {"status": HIDDEN, "score": score, "len": ln}
            else:
                runs = _runs(vis | ur, ns)
                if not runs:
                    out[i] = {"status": HIDDEN, "score": score, "len": ln}
                else:
                    out[i] = {"status": SPLIT, "score": score, "len": ln, "runs": runs}
    return out


def s_len(meta) -> float:
    kind, pts = meta[3], meta[4]
    poly = list(pts) if kind == "l" else g2.bezier(*pts, n=16)
    return sum(math.hypot(poly[q + 1][0] - poly[q][0], poly[q + 1][1] - poly[q][1])
               for q in range(len(poly) - 1))


def _along(poly, ns: int):
    """``ns`` sample points evenly spaced along a polyline (cell centers)
    with unit tangents."""
    a = np.asarray(poly, dtype=float)
    seg = a[1:] - a[:-1]
    sl = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(sl)])
    total = cum[-1]
    s = (np.arange(ns) + 0.5) / ns * total
    idx = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(seg) - 1)
    safe = np.where(sl[idx] > 1e-12, sl[idx], 1.0)
    t = (s - cum[idx]) / safe
    px = a[idx, 0] + seg[idx, 0] * t
    py = a[idx, 1] + seg[idx, 1] * t
    return px, py, seg[idx, 0] / safe, seg[idx, 1] / safe


def _spread(vis: np.ndarray, k: int) -> np.ndarray:
    """Dashed strokes: a sample counts when any sample within one dash
    period is inked."""
    if k <= 1:
        return vis
    n = len(vis)
    c = np.concatenate([[0], np.cumsum(vis.astype(np.int64))])
    lo = np.clip(np.arange(n) - k, 0, n)
    hi = np.clip(np.arange(n) + k + 1, 0, n)
    return (c[hi] - c[lo]) > 0


def _runs(vis: np.ndarray, ns: int, min_run: int = 2) -> list:
    """Visible sample runs -> ``[(t0, t1)]`` parameter intervals."""
    runs = []
    start = None
    for i in range(ns):
        if vis[i] and start is None:
            start = i
        elif not vis[i] and start is not None:
            if i - start >= min_run:
                runs.append((start / ns, i / ns))
            start = None
    if start is not None and ns - start >= min_run:
        runs.append((start / ns, 1.0))
    return runs


def _small_pass_strokes(page, s_meta, judged, small_ix) -> None:
    """Judge short strokes on 300 dpi tiles."""
    tiles: dict = {}
    for i in small_ix:
        kind, pts = s_meta[i][3], s_meta[i][4]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        tiles.setdefault((int(cx // TILE_PT), int(cy // TILE_PT)), []).append(i)
    pad = 12.0
    for (tx, ty), ix in tiles.items():
        clip = (tx * TILE_PT - pad, ty * TILE_PT - pad,
                (tx + 1) * TILE_PT + pad, (ty + 1) * TILE_PT + pad)
        rd = Render(page, DPI_SMALL, clip=clip)
        if rd.w < 2 or rd.h < 2:
            continue
        got = _judge_strokes(rd, s_meta, 0.0, small_pass=True, only=ix)
        for i in ix:
            if got[i] is not None:
                judged[i] = got[i]


# ------------------------------------------------------------------ fills --

def _judge_fill(rd: Render, fm, small_pass: bool = False) -> dict:
    seq, d, polys, curved, clip_geo, rgb, opacity = fm
    area_vec = _poly_area(polys)
    base = {"polys": polys, "area_vector": area_vec, "area_visible": area_vec,
            "score": 1.0, "obscured": 0.0, "method": "vector"}
    if math.dist(rgb, PAPER) < FAINT:
        return dict(base, status=MASK, why="filled in the paper color (a mask)")
    want = _blend(rgb, opacity)
    transparent = opacity is not None and opacity < 0.999
    pts = [p for poly in polys for p in poly]
    x0, y0, x1, y1 = g2.bbox(pts)
    (px0,), (py0,) = rd.px([x0], [y0])
    (px1,), (py1,) = rd.px([x1], [y1])
    if min(px1 - px0, py1 - py0) < 3.0 and not small_pass:
        return dict(base, status="small")
    c0 = max(0, int(math.floor(px0)) - 2)
    r0 = max(0, int(math.floor(py0)) - 2)
    c1 = min(rd.w, int(math.ceil(px1)) + 2)
    r1 = min(rd.h, int(math.ceil(py1)) + 2)
    w, h = c1 - c0, r1 - r0
    if w <= 0 or h <= 0:
        return dict(base, status=HIDDEN, area_visible=0.0, score=0.0,
                    why="outside the plotted sheet")

    def to_local(poly):
        ax, ay = rd.px([p[0] for p in poly], [p[1] for p in poly])
        return list(zip((ax - c0).tolist(), (ay - r0).tolist()))

    even_odd = bool(d.get("even_odd"))
    m_vec = g2.raster([to_local(p) for p in polys], w, h, even_odd=even_odd)
    n_vec = int(m_vec.sum())
    if n_vec < 4:
        return dict(base, status="small" if not small_pass else UNREADABLE,
                    why="too small to judge even at 300 dpi")
    m_exp = m_vec
    for cpolys, c_eo in clip_geo:
        if cpolys:
            m_exp = m_exp & g2.raster([to_local(p) for p in cpolys], w, h,
                                      even_odd=c_eo)
        else:
            m_exp = np.zeros_like(m_vec)       # an empty clip shows nothing
    n_exp = int(m_exp.sum())
    if n_exp == 0:
        return dict(base, status=HIDDEN, area_visible=0.0, score=0.0,
                    why="clipped away entirely by a clip path")
    core = g2.erode(m_exp, 1)
    n_core = int(core.sum())
    if n_core < 6:
        if not small_pass:
            return dict(base, status="small")
        core, n_core = m_exp, n_exp
    match = rd.patch_match(want, r0, r1, c0, c1)
    bad = core & ~match
    n_match = n_core - int(bad.sum())
    if n_match >= 0.5 * n_core:
        blob = g2.opening(bad, THIN_PX)          # thick missing parts only
    else:
        blob = bad
    n_blob = int(blob.sum())
    thin = int(bad.sum()) - n_blob
    share = (n_core - n_blob) / n_core            # visible share of the expected region
    clip_ratio = n_exp / n_vec
    area_exp = area_vec * clip_ratio
    area_vis = area_exp * share
    score = area_vis / area_vec if area_vec > 0 else 0.0
    obscured = thin / n_core
    if transparent and share < FILL_KEEP:
        return dict(base, status=UNREADABLE, obscured=obscured,
                    why="transparent fill does not match its blend on the render; "
                        "cannot be judged")
    if score >= FILL_KEEP:
        return dict(base, status=VISIBLE, area_visible=area_vis, score=score,
                    obscured=obscured)
    if score < FILL_GONE:
        return dict(base, status=HIDDEN, area_visible=area_vis, score=score,
                    why="hidden: not on the plotted sheet (under a later fill "
                        "or mask)" if clip_ratio > FILL_GONE else
                        "clipped away by a clip path")
    # ---- partly visible: build the visible polygon ------------------------ #
    if share >= FILL_KEEP:
        exact = _vector_clip(polys, clip_geo, even_odd)
        if exact is not None:
            return dict(base, status=CLIPPED, polys=exact, area_visible=_poly_area(exact),
                        score=_poly_area(exact) / area_vec, obscured=obscured,
                        method="vector",
                        why="limited by a clip path; the visible part is the exact "
                            "vector clip")
        seen = m_exp
        why = "limited by a clip path; visible part traced off the render"
    else:
        seen = m_exp & ~g2.dilate(blob, 1)
        why = "partly hidden on the plotted sheet; visible part traced off the render"
    traced = []
    for loop in g2.trace(seen):
        if abs(g2.area(loop)) < 6.0:
            continue
        poly = g2.refit(loop)
        traced.append([((x + c0) / rd.zoom + rd.ox, (y + r0) / rd.zoom + rd.oy)
                       for x, y in poly])
    if not traced:
        return dict(base, status=HIDDEN, area_visible=area_vis, score=score,
                    why="hidden: nothing of it could be traced on the plotted sheet")
    return dict(base, status=CLIPPED, polys=traced, area_visible=area_vis, score=score,
                obscured=obscured, method="traced", why=why)


def _vector_clip(polys, clip_geo, even_odd) -> list | None:
    """Exact vector clip when it is computable: a single non-self-nesting
    subject outline against convex clip outlines.  ``None`` otherwise."""
    if len(polys) != 1 or even_odd:
        return None
    cur = [polys[0]]
    for cpolys, c_eo in clip_geo:
        if len(cpolys) != 1 or c_eo or not g2.is_convex(cpolys[0], 1e-7):
            return None
        nxt = []
        for p in cur:
            q = g2.clip_convex(p, cpolys[0])
            if q:
                nxt.append(g2.drop_collinear(q, 1e-6))
        cur = nxt
        if not cur:
            return None
    return cur


def _small_pass_fills(page, res: Result, small_f) -> None:
    tiles: dict = {}
    for n, fm in small_f:
        pts = [p for poly in fm[2] for p in poly]
        x0, y0, x1, y1 = g2.bbox(pts)
        tiles.setdefault((int(((x0 + x1) / 2) // TILE_PT),
                          int(((y0 + y1) / 2) // TILE_PT)), []).append(fm)
    pad = 12.0
    for (tx, ty), fms in tiles.items():
        clip = (tx * TILE_PT - pad, ty * TILE_PT - pad,
                (tx + 1) * TILE_PT + pad, (ty + 1) * TILE_PT + pad)
        rd = Render(page, DPI_SMALL, clip=clip)
        for fm in fms:
            if rd.w < 2 or rd.h < 2:
                out = {"status": HIDDEN, "polys": fm[2], "why": "outside the plotted sheet",
                       "area_vector": _poly_area(fm[2]), "area_visible": 0.0,
                       "score": 0.0, "obscured": 0.0, "method": "vector"}
            else:
                out = _judge_fill(rd, fm, small_pass=True)
            _file_fill(res, fm, out)


# ----------------------------------------------------------- reverse check --

def unread_regions(rd: Render, rgb, accepted, *, min_area_pt2: float = 4.0,
                   tol: float = COLOR_TOL) -> list:
    """The reverse check: pixels of a tracked fill class that no accepted
    element covers ("seen but not read").

    ``accepted`` is a list of polygons in viewer points.  Returns
    ``[{"bbox": [x0, y0, x1, y1], "area": pt^2, "center": (x, y)}]`` for
    every leftover region of at least ``min_area_pt2``."""
    cls = rd.class_mask(rgb, tol)
    cover = np.zeros((rd.h, rd.w), dtype=bool)
    for poly in accepted:
        ax, ay = rd.px([p[0] for p in poly], [p[1] for p in poly])
        x0 = max(0, int(math.floor(ax.min())) - 2)
        y0 = max(0, int(math.floor(ay.min())) - 2)
        x1 = min(rd.w, int(math.ceil(ax.max())) + 2)
        y1 = min(rd.h, int(math.ceil(ay.max())) + 2)
        if x1 <= x0 or y1 <= y0:
            continue
        m = g2.raster([list(zip((ax - x0).tolist(), (ay - y0).tolist()))],
                      x1 - x0, y1 - y0)
        cover[y0:y1, x0:x1] |= m
    left = cls & ~g2.dilate(cover, 2)
    left = g2.opening(left, 1)
    if not left.any():
        return []
    labels, boxes = g2.label(left)
    out = []
    px_area = 1.0 / (rd.zoom * rd.zoom)
    for b in boxes:
        a = b.area * px_area
        if a < min_area_pt2:
            continue
        out.append({"bbox": [round(b.x0 / rd.zoom + rd.ox, 2), round(b.y0 / rd.zoom + rd.oy, 2),
                             round((b.x1 + 1) / rd.zoom + rd.ox, 2),
                             round((b.y1 + 1) / rd.zoom + rd.oy, 2)],
                    "area": round(a, 2),
                    "center": (round((b.x0 + b.x1 + 1) / 2 / rd.zoom + rd.ox, 2),
                               round((b.y0 + b.y1 + 1) / 2 / rd.zoom + rd.oy, 2))})
    return out


def stroke_present(rd: Render, a, b, rgb, width_pt: float) -> float:
    """Share of the segment a-b (viewer points) along which ink of the
    given stroke class is on the render.  The picture check uses this to
    compare drawn edges with the source sheet."""
    meta = [(0, 0, 0, "l", [tuple(a), tuple(b)], tuple(rgb), float(width_pt), "", False)]
    got = _judge_strokes(rd, meta, 0.0, small_pass=True)[0]
    if got is None or got["status"] in ("skip", MASK, UNREADABLE):
        return 0.0
    return float(got["score"])


# ----------------------------------------------------------- the drop-in --

_CACHE: dict = {}
_CACHE_MAX = 8


def get_drawings(page, *, sightline: bool | None = None, min_len_pt: float = 0.0,
                 record=None) -> list:
    """Drop-in for ``page.get_drawings()`` that returns only what is on
    the plotted sheet.  ``sightline=None`` follows :func:`enabled`;
    ``False`` returns the raw vectors.  ``record`` may be a list that
    receives the page's :class:`PageRecord`."""
    on = enabled() if sightline is None else bool(sightline)
    if not on:
        return page.get_drawings()
    res = gate_cached(page, min_len_pt=min_len_pt)
    if record is not None:
        record.append(res.record)
    return res.drawings()


def has_visible_content(page, *, sightline: bool | None = None) -> bool:
    """True when the page has drawn content that is on the plotted sheet
    (paper-colored masks and hidden geometry do not count).  With the
    Sightline switched off: any vector path at all (the raw reading)."""
    on = enabled() if sightline is None else bool(sightline)
    if not on:
        return bool(page.get_drawings())
    res = gate_cached(page, min_len_pt=2.0)
    if not res.registered:
        return bool(res.paths)
    return (any(st.status != MASK for st in res.strokes)
            or any(f.status != MASK for f in res.fills))


def gate_cached(page, *, dpi: int = DPI, min_len_pt: float = 0.0) -> Result:
    """:func:`gate` with a small per-process cache keyed on the document,
    the page and its content."""
    base = _page_key(page, dpi)
    if base is None:
        return gate(page, dpi=dpi, min_len_pt=min_len_pt)
    key = base + (float(min_len_pt),)
    res = _CACHE.get(key)
    if res is None:
        res = gate(page, dpi=dpi, min_len_pt=min_len_pt)
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = res
    return res
