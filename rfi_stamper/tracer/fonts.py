"""Synthetic character glyph sources — no downloads, no proprietary fonts.

Two font families feed the Tracer, both license-clean and fully offline:

* **base-14 outlines** (OCR_PLAN §3 source A) — *Helvetica* (modern title-block
  lettering; the app and tests render in fitz Helvetica, so these make clean
  reads reliable) and *Courier* (uniform-stroke monospace, a fair proxy for CAD
  ISO-3098 technical lettering).  ``prototypes()`` builds the P1/ensemble **NCC
  template bank** from these, unchanged from P1.
* **single-stroke vector glyphs** (OCR_PLAN §3 source B) — a compact
  ISO-3098-style *simplex* stroke table in the public-domain **Hershey**
  lineage (NBS 1967), authored here for the closed CHARSET so there is no
  licensing question and no proprietary CAD ``*.shx`` font is named or shipped.
  Each glyph is a set of pen polylines; they are stroked in numpy at pen width
  ``h/10`` (Type B) and ``h/14`` (Type A), at 0° and 15° slant.  These broaden
  the **synthetic training corpus** (``synth.py``) beyond the two outline faces.

``glyph_images(char, sizes, dpi)`` returns the clean glyph rasters across every
source/style so the corpus generator can degrade them; ``prototypes()`` stays
base-14-only (the NCC bank is the high-precision voter, not a training set).

CHARSET is uppercase + digits + the common technical marks (OCR_PLAN §6):
the all-uppercase convention halves the class count and removes case ambiguity.
"""
from __future__ import annotations

import numpy as np
import fitz

from . import binarize
from .normalize import CELL, norm_glyph

CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.\"'/#&"

# base-14 PostScript names for the two prototype faces
_FACES = ("helv", "cour")

_CACHE: dict[int, dict] = {}


def _render_glyph_ink(ch: str, fontname: str, fontsize: int = 120):
    """Render a single glyph and return its ink bitmap (True = ink), or None.

    The glyph is drawn onto a scratch page well clear of the margins, rendered
    to a grayscale pixmap, Otsu-binarized, and returned.  ``None`` if the glyph
    left no ink (a face lacking the mark).
    """
    doc = fitz.open()
    try:
        page = doc.new_page(width=fontsize * 3, height=fontsize * 3)
        # baseline low enough that ascenders/quotes are not clipped
        page.insert_text((fontsize, fontsize * 2), ch, fontname=fontname,
                         fontsize=fontsize)
        pix = page.get_pixmap(colorspace=fitz.csGRAY, alpha=False)
        gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    finally:
        doc.close()
    ink = binarize.otsu(gray)[1]
    if not ink.any():
        return None
    return ink


def prototypes(cell: int = CELL) -> dict:
    """Return ``{char: (cells (n, cell, cell) float32, aspects (n,) float32)}``.

    Built once and cached.  Each class carries up to two variants (Helvetica,
    Courier); a class keeps at least one.  Alongside each normalized template we
    keep its raw ink aspect ratio (width/height) — the shape-only cell cannot
    separate marks that normalize to the same blob (a period vs. an apostrophe),
    so the classifier folds aspect in as a light structural tie-break.  ``cell``
    other than the module CELL forces a rebuild (rare; tests use the default).
    """
    if cell in _CACHE:
        return _CACHE[cell]
    bank: dict[str, tuple] = {}
    for ch in CHARSET:
        cells, aspects = [], []
        for face in _FACES:
            ink = _render_glyph_ink(ch, face)
            if ink is None:
                continue
            ng = norm_glyph(ink)
            cells.append(ng.cell)
            aspects.append(ng.aspect)
        if cells:
            bank[ch] = (np.stack(cells).astype(np.float32),
                        np.asarray(aspects, np.float32))
    _CACHE[cell] = bank
    return bank


# --------------------------------------------------------------------------- #
#  Single-stroke ISO-3098-style vector glyphs (Hershey simplex lineage)        #
# --------------------------------------------------------------------------- #
#
# Coordinate grid: baseline y = 0, cap top y = 7, nominal advance x ≈ 0..4
# (ISO char width ≈ 0.6·h).  Each glyph is a list of *strokes*; each stroke is a
# polyline of (x, y) vertices drawn pen-down.  Marks sit at their true vertical
# band (hyphen mid, period baseline, quotes at cap) — that band is what the
# 2-line vertical-position feature reads.  Authored fresh for this subset in the
# public-domain single-stroke tradition; no proprietary font data is embedded.

_GRID_CAP = 7.0

HERSHEY: dict[str, list] = {
    "A": [[(0, 0), (2, 7), (4, 0)], [(0.7, 2.5), (3.3, 2.5)]],
    "B": [[(0, 0), (0, 7)],
          [(0, 7), (2.6, 7), (3.5, 6), (3.5, 4.6), (2.6, 3.8), (0, 3.8)],
          [(0, 3.8), (2.9, 3.8), (3.8, 2.6), (3.7, 1), (2.6, 0), (0, 0)]],
    "C": [[(4, 5.4), (2.9, 6.9), (1.1, 6.9), (0, 5.4), (0, 1.6), (1.1, 0),
           (2.9, 0), (4, 1.6)]],
    "D": [[(0, 0), (0, 7)],
          [(0, 7), (2.4, 7), (4, 5.2), (4, 1.8), (2.4, 0), (0, 0)]],
    "E": [[(4, 7), (0, 7), (0, 0), (4, 0)], [(0, 3.4), (3.1, 3.4)]],
    "F": [[(4, 7), (0, 7), (0, 0)], [(0, 3.4), (3.0, 3.4)]],
    "G": [[(4, 5.4), (2.9, 6.9), (1.1, 6.9), (0, 5.4), (0, 1.6), (1.1, 0),
           (2.9, 0), (4, 1.6), (4, 3), (2.5, 3)]],
    "H": [[(0, 0), (0, 7)], [(4, 0), (4, 7)], [(0, 3.5), (4, 3.5)]],
    "I": [[(2, 0), (2, 7)], [(1, 7), (3, 7)], [(1, 0), (3, 0)]],
    "J": [[(3.2, 7), (3.2, 1.6), (2.6, 0.2), (1.2, 0), (0.2, 1.4)]],
    "K": [[(0, 0), (0, 7)], [(4, 7), (0, 3.4)], [(1.2, 4.2), (4, 0)]],
    "L": [[(0, 7), (0, 0), (4, 0)]],
    "M": [[(0, 0), (0, 7), (2, 3.2), (4, 7), (4, 0)]],
    "N": [[(0, 0), (0, 7), (4, 0), (4, 7)]],
    "O": [[(2, 7), (0.6, 6.3), (0, 3.5), (0.6, 0.7), (2, 0), (3.4, 0.7),
           (4, 3.5), (3.4, 6.3), (2, 7)]],
    "P": [[(0, 0), (0, 7)],
          [(0, 7), (2.8, 7), (3.7, 6), (3.7, 4.8), (2.8, 3.9), (0, 3.9)]],
    "Q": [[(2, 7), (0.6, 6.3), (0, 3.5), (0.6, 0.7), (2, 0), (3.4, 0.7),
           (4, 3.5), (3.4, 6.3), (2, 7)], [(2.5, 1.6), (4.2, -0.2)]],
    "R": [[(0, 0), (0, 7)],
          [(0, 7), (2.8, 7), (3.7, 6), (3.7, 4.8), (2.8, 3.9), (0, 3.9)],
          [(1.6, 3.9), (4, 0)]],
    "S": [[(4, 5.6), (2.9, 6.9), (1.1, 6.9), (0, 5.7), (0.7, 4.3), (3.3, 3.1),
           (4, 1.7), (2.9, 0.1), (1.1, 0.1), (0, 1.4)]],
    "T": [[(0, 7), (4, 7)], [(2, 7), (2, 0)]],
    "U": [[(0, 7), (0, 1.6), (1.1, 0), (2.9, 0), (4, 1.6), (4, 7)]],
    "V": [[(0, 7), (2, 0), (4, 7)]],
    "W": [[(0, 7), (1, 0), (2, 4), (3, 0), (4, 7)]],
    "X": [[(0, 0), (4, 7)], [(0, 7), (4, 0)]],
    "Y": [[(0, 7), (2, 3.4), (4, 7)], [(2, 3.4), (2, 0)]],
    "Z": [[(0, 7), (4, 7), (0, 0), (4, 0)]],
    "0": [[(2, 7), (0.7, 6.2), (0.2, 3.5), (0.7, 0.8), (2, 0), (3.3, 0.8),
           (3.8, 3.5), (3.3, 6.2), (2, 7)]],
    "1": [[(1, 5.6), (2, 7), (2, 0)], [(1, 0), (3.1, 0)]],
    "2": [[(0.2, 5.5), (1.1, 7), (3, 7), (4, 5.6), (3.9, 4.3), (0, 0), (4, 0)]],
    "3": [[(0.2, 7), (4, 7), (2, 4), (3.4, 3.5), (3.7, 1.8), (2.7, 0.1),
           (1, 0.1), (0, 1.3)]],
    "4": [[(3, 0), (3, 7), (0, 2.6), (4, 2.6)]],
    "5": [[(4, 7), (1, 7), (0.6, 4.2), (1.3, 4.5), (3, 4.5), (4, 3.2),
           (4, 1.6), (2.9, 0.1), (1.1, 0.1), (0, 1.3)]],
    "6": [[(3.4, 6), (2.4, 7), (1, 7), (0, 5.1), (0, 1.8), (1, 0.1),
           (2.6, 0.1), (3.6, 1.4), (3.6, 2.9), (2.6, 4.1), (1, 4.1), (0, 3.1)]],
    "7": [[(0, 7), (4, 7), (1.5, 0)]],
    "8": [[(2, 3.9), (0.8, 4.6), (0.8, 6.2), (2, 7), (3.2, 6.2), (3.2, 4.6),
           (2, 3.9), (0.6, 3.1), (0.5, 1.1), (2, 0), (3.5, 1.1), (3.4, 3.1),
           (2, 3.9)]],
    "9": [[(0.6, 1), (1.6, 0), (3, 0), (4, 1.9), (4, 5.2), (3, 7), (1.4, 7),
           (0.4, 5.9), (0.4, 4.3), (1.4, 3.1), (3, 3.1), (4, 4.1)]],
    "-": [[(0.5, 3.5), (3.5, 3.5)]],
    ".": [[(1.9, 0.15), (2.1, 0.15), (2.1, 0.55), (1.9, 0.55), (1.9, 0.15)]],
    "\"": [[(1.3, 7), (1.3, 5.4)], [(2.7, 7), (2.7, 5.4)]],
    "'": [[(2, 7), (2, 5.4)]],
    "/": [[(0.3, 0), (3.7, 7)]],
    "#": [[(0.5, 2.2), (3.6, 2.6)], [(0.4, 4.6), (3.5, 5.0)],
          [(1.5, 0.4), (1.1, 6.6)], [(3.0, 0.4), (2.6, 6.6)]],
    "&": [[(4, 0), (1.4, 3.9), (0.6, 5.4), (1.3, 6.8), (2.5, 6.8), (3, 5.6),
           (2.3, 4.3), (0.6, 1.6), (1.2, 0.2), (2.5, 0.2), (3.6, 1.6),
           (4, 3.1)]],
}

# style ids used across sources for corpus font-holdout bookkeeping
FONT_IDS = ("helv", "cour", "herA0", "herA15", "herB0", "herB15")


def _stroke_glyph(strokes, cap_px: int, pen_px: float, slant_deg: float):
    """Rasterize single-stroke polylines → grayscale uint8 (dark ink on white).

    Anti-aliased: each pixel's ink coverage is ``clip(r + 0.5 − dist, 0, 1)``
    against the nearest stroke segment (``r`` = pen radius).  A 15° slant is a
    horizontal shear ``x += y·tan(15°)``.
    """
    scale = cap_px / _GRID_CAP
    tan = np.tan(np.deg2rad(slant_deg))
    r = max(0.5, pen_px / 2.0)
    segs = []
    xs_all, ys_all = [], []
    for stroke in strokes:
        pts = []
        for (gx, gy) in stroke:
            x = (gx + gy * tan) * scale
            y = (_GRID_CAP - gy) * scale         # flip: image row 0 = top
            pts.append((x, y))
            xs_all.append(x); ys_all.append(y)
        for i in range(len(pts) - 1):
            segs.append((pts[i], pts[i + 1]))
        if len(pts) == 1:
            segs.append((pts[0], pts[0]))
    margin = r + 2.0
    minx, maxx = min(xs_all) - margin, max(xs_all) + margin
    miny, maxy = min(ys_all) - margin, max(ys_all) + margin
    W = max(3, int(np.ceil(maxx - minx)))
    H = max(3, int(np.ceil(maxy - miny)))
    yy, xx = np.mgrid[0:H, 0:W]
    px = xx + minx
    py = yy + miny
    cover = np.zeros((H, W), np.float64)
    for (ax, ay), (bx, by) in segs:
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        if L2 < 1e-9:
            dist = np.sqrt((px - ax) ** 2 + (py - ay) ** 2)
        else:
            t = ((px - ax) * vx + (py - ay) * vy) / L2
            t = np.clip(t, 0.0, 1.0)
            projx = ax + t * vx
            projy = ay + t * vy
            dist = np.sqrt((px - projx) ** 2 + (py - projy) ** 2)
        cover = np.maximum(cover, np.clip(r + 0.5 - dist, 0.0, 1.0))
    gray = (255.0 * (1.0 - cover)).astype(np.uint8)
    return gray


def _hershey_gray(ch: str, cap_px: int, font_id: str):
    """Render one Hershey CHARSET glyph → grayscale, or None if unavailable."""
    strokes = HERSHEY.get(ch)
    if strokes is None:
        return None
    if font_id in ("herA0", "herA15"):
        pen = cap_px / 14.0                       # ISO Type A
    else:
        pen = cap_px / 10.0                       # ISO Type B
    slant = 15.0 if font_id.endswith("15") else 0.0
    return _stroke_glyph(strokes, cap_px, pen, slant)


def _fitz_gray(ch: str, cap_px: int):
    """Render one base-14 glyph to grayscale at a target cap height, per face."""
    fs = max(8, int(round(cap_px / 0.70)))
    out = {}
    for face in _FACES:
        doc = fitz.open()
        try:
            page = doc.new_page(width=fs * 3, height=fs * 3)
            page.insert_text((fs, fs * 2), ch, fontname=face, fontsize=fs)
            pix = page.get_pixmap(colorspace=fitz.csGRAY, alpha=False)
            g = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width)
        finally:
            doc.close()
        ink = g < 250
        if not ink.any():
            continue
        ys, xs = np.where(ink)
        out[face] = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy()
    return out


def glyph_images(char: str, sizes=(20, 32, 48), dpi: int = 300):
    """Clean glyph rasters for ``char`` across every source/style/size.

    Returns ``[(font_id, gray_uint8), ...]`` (dark ink on white).  Font ids come
    from :data:`FONT_IDS`; ``dpi`` is accepted for signature parity (sizes are
    already px cap-heights).  A source that cannot render ``char`` is skipped.
    """
    out = []
    for cap in sizes:
        fz = _fitz_gray(char, cap)
        if "helv" in fz:
            out.append(("helv", fz["helv"]))
        if "cour" in fz:
            out.append(("cour", fz["cour"]))
        for fid in ("herA0", "herA15", "herB0", "herB15"):
            g = _hershey_gray(char, cap, fid)
            if g is not None:
                out.append((fid, g))
    return out
