"""Synthetic character prototypes — no downloads, no proprietary fonts.

The Tracer's template bank is generated at first use by rendering each class of
the closed construction CHARSET with the two license-clean base-14 typefaces
already inside PyMuPDF (OCR_PLAN §3 font source A):

* **Helvetica** — stands in for modern title-block lettering.  It matters most
  here: the app and the test-suite render text in fitz Helvetica, so Helvetica
  prototypes make clean reads reliable.
* **Courier** — a uniform-stroke monospace, a fair proxy for CAD ISO-3098-style
  technical lettering.

Each glyph is rendered large, binarized, and pushed through the same
``norm_glyph`` protocol the scanned candidates take, so prototypes and
candidates live in one comparison space.  Public-domain Hershey single-stroke
fonts (OCR_PLAN §3 source B) and the missing marks (source C) are a P2 upgrade;
base-14 is sufficient for the P1 clean-read bar.  The bank is cached at module
level so the fitz rendering happens exactly once.

CHARSET is uppercase + digits + the common technical marks (OCR_PLAN §6):
all-uppercase convention halves the class count and removes case ambiguity.
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
