"""Raster acquisition + polarity / size normalization for the Tracer.

Turns a PDF page into the grayscale image the rest of the pipeline reads, and
makes two size/polarity decisions up front (OCR_PLAN §2 steps 1–2):

* **Polarity** is decided from the **title-block corner** (bottom-right window),
  not the whole page — a mostly-dark drawing with a light title block must not
  be inverted.  If that corner's background mode is dark (< 128) the sheet is a
  white-on-black negative and is inverted so ink is dark everywhere.
* **Effective cap-height** is measured (rough Otsu → connected-component median)
  and, if the lettering is small, the raster is bilinearly upscaled toward the
  20–30 px x-height sweet spot.  Below the ~14 px per-glyph floor the page is
  flagged ``too_small`` and **never upscaled-and-pretended** — the honest
  degrade stance of OCR_PLAN §8: flag, don't fabricate.

``render_gray`` returns ``(gray uint8 HxW, meta)`` where ``meta`` carries the
scale actually applied and ``px_per_pt`` for mapping pixels back to viewer page
points downstream.
"""
from __future__ import annotations

import numpy as np
import fitz

from . import binarize, components

# --- OCR_PLAN §5 size targets (px) ------------------------------------------
TARGET_CAP = 30          # upscale toward this cap-height
CAP_UPSCALE_BELOW = 20   # only upscale when measured cap-height is under this
MIN_GLYPH_CAP = 14       # per-glyph floor; below → too_small flag
MAX_UPSCALE = 4.0        # never magnify more than this
POLARITY_DARK = 128      # corner background mode below this → invert
CORNER_FRAC = 0.25       # title-block window = right 25% × bottom 25%


def _bilinear(gray: np.ndarray, scale: float) -> np.ndarray:
    """Bilinear resample by ``scale`` (numpy only; used only to upscale)."""
    H, W = gray.shape
    nH, nW = int(round(H * scale)), int(round(W * scale))
    ys = (np.arange(nH) + 0.5) / scale - 0.5
    xs = (np.arange(nW) + 0.5) / scale - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, H - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, W - 1)
    y1 = np.clip(y0 + 1, 0, H - 1)
    x1 = np.clip(x0 + 1, 0, W - 1)
    wy = np.clip(ys - np.floor(ys), 0, 1)[:, None]
    wx = np.clip(xs - np.floor(xs), 0, 1)[None, :]
    g = gray.astype(np.float64)
    top = g[y0][:, x0] * (1 - wx) + g[y0][:, x1] * wx
    bot = g[y1][:, x0] * (1 - wx) + g[y1][:, x1] * wx
    return np.clip(top * (1 - wy) + bot * wy, 0, 255).astype(np.uint8)


def _decide_polarity(gray: np.ndarray) -> bool:
    """True if the title-block corner reads white-on-black (needs inversion)."""
    H, W = gray.shape
    y0 = int(H * (1 - CORNER_FRAC))
    x0 = int(W * (1 - CORNER_FRAC))
    corner = gray[y0:, x0:]
    if corner.size == 0:
        corner = gray
    mode = int(np.bincount(corner.ravel(), minlength=256).argmax())
    return mode < POLARITY_DARK


def _estimate_cap(gray: np.ndarray) -> float:
    """Rough cap-height = median height of plausibly-glyph-sized components."""
    ink = binarize.otsu(gray)[1]
    _, boxes = components.label(ink)
    if not boxes:
        return 0.0
    hs = [b.h for b in boxes
          if components.CAP_LO <= b.h <= 4 * components.CAP_HI and b.w >= 2]
    return float(np.median(hs)) if hs else 0.0


def _as_gray(page) -> tuple[np.ndarray, float, float]:
    """Render one fitz page to a grayscale array; return (gray, xres, yres)."""
    pix = page.get_pixmap(colorspace=fitz.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()
    return gray, float(pix.xres), float(pix.yres)


def render_gray(page_or_path, page_no: int = 1, dpi: int = 300):
    """Render a page to grayscale and normalize polarity + size.

    ``page_or_path`` may be a path, an open ``fitz.Document`` or a ``fitz.Page``.
    Returns ``(gray uint8, meta)`` where meta = ``{dpi, scale, w, h, inverted,
    cap_px, too_small, px_per_pt}``.  ``px_per_pt`` is the raster-pixels-per-
    point factor after any upscale, so ``point = pixel / px_per_pt`` recovers
    viewer page coordinates.
    """
    close = None
    if isinstance(page_or_path, str):
        close = fitz.open(page_or_path)
        page = close[page_no - 1]
    elif isinstance(page_or_path, fitz.Document):
        page = page_or_path[page_no - 1]
    else:
        page = page_or_path
    try:
        page.set_rotation  # attribute probe; a Page has it
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
        gray = np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height, pix.width).copy()
    finally:
        if close is not None:
            close.close()

    inverted = _decide_polarity(gray)
    if inverted:
        gray = (255 - gray).astype(np.uint8)

    cap_px = _estimate_cap(gray)
    ink_any = float((gray < 250).mean()) > 1e-3
    scale = 1.0
    too_small = False
    if 0 < cap_px < MIN_GLYPH_CAP:
        # below the recovery floor: flag and refuse — magnifying sub-legible
        # text just enlarges the mush (OCR_PLAN §8, "never upscale-and-pretend").
        too_small = True
    elif MIN_GLYPH_CAP <= cap_px < CAP_UPSCALE_BELOW:
        # recoverable but small: upscale toward the 20–30 px sweet spot.
        scale = min(MAX_UPSCALE, TARGET_CAP / cap_px)
        if scale > 1.01:
            gray = _bilinear(gray, scale)
    elif cap_px == 0 and ink_any:
        # ink present but no glyph-sized component resolved → degraded/tiny.
        too_small = True

    meta = {
        "dpi": dpi,
        "scale": scale,
        "w": gray.shape[1],
        "h": gray.shape[0],
        "inverted": inverted,
        "cap_px": float(cap_px),
        "too_small": too_small,
        "px_per_pt": (dpi / 72.0) * scale,
    }
    return gray, meta
