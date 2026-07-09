"""Glyph normalization to a fixed cell (the MNIST-style protocol).

Every candidate character — whether harvested from a scan or rendered as a
synthetic prototype — is reduced to one canonical bitmap so the classifier
compares like with like.  The protocol (OCR_PLAN §9, §5 "Glyph cell"):

1. Crop to the ink bounding box.
2. Aspect-preserving **area-average** downsample so the longer side is 20 px.
   Area-average (a proper box filter) is mandatory: nearest-neighbour drops
   thin single-stroke lettering and stretch-to-square distorts ``I`` into an
   ``O``-like blob.  A 2-px stroke survives because its ink mass is *summed*
   into the coarse cell, not sampled.
3. Center by center-of-mass inside a ``CELL``×``CELL`` (28×28) frame.

Two extra scalars ride along — the raw aspect ratio and a baseline-relative
vertical position — because shape alone cannot separate marks that normalize
to the same blob (a period vs. an apostrophe); P1's NCC uses the cell, later
phases fold in the scalars.  Returned cells are ``float32`` in ``[0, 1]``.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

CELL = 28          # output frame (OCR_PLAN §5: center-of-mass in 28×28)
FIT = 20           # longer side fitted to 20 px before centering


class Norm(NamedTuple):
    cell: np.ndarray   # (CELL, CELL) float32 in [0, 1]
    aspect: float      # ink-bbox width / height (raw, pre-normalization)
    rel_y: float       # bbox-center y relative to its supplied text band


def _area_downsample(src: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """Exact area-average box downsample via two binning matrices.

    ``R @ src @ C`` sums the source pixels falling in each output block (each
    source pixel is assigned to exactly one block); dividing by the block area
    yields the average.  Fully vectorized and, crucially, ink-preserving.
    """
    H, W = src.shape
    s = src.astype(np.float64)
    row_bin = np.minimum(np.arange(H) * out_h // H, out_h - 1)
    col_bin = np.minimum(np.arange(W) * out_w // W, out_w - 1)
    R = np.zeros((out_h, H))
    R[row_bin, np.arange(H)] = 1.0
    C = np.zeros((W, out_w))
    C[np.arange(W), col_bin] = 1.0
    num = R @ s @ C
    den = R @ np.ones_like(s) @ C
    return num / np.maximum(den, 1.0)


def norm_glyph(binary_crop: np.ndarray, band=None) -> Norm:
    """Normalize one glyph bitmap to the canonical cell (see module docstring).

    ``binary_crop`` is an ink boolean (or 0/1) array; it is cropped to its own
    ink first, so callers may pass a loose window.  ``band`` is an optional
    ``(top, bottom)`` row pair (in the same frame as the crop) used only to
    compute ``rel_y``; when absent ``rel_y`` is 0.5.  Empty input yields a zero
    cell (honest: nothing in, nothing out).
    """
    a = np.asarray(binary_crop)
    if a.dtype != bool:
        a = a > 0
    ys, xs = np.where(a)
    if ys.size == 0:
        return Norm(np.zeros((CELL, CELL), np.float32), 1.0, 0.5)
    y0, y1 = ys.min(), ys.max()
    x0, x1 = xs.min(), xs.max()
    crop = a[y0:y1 + 1, x0:x1 + 1].astype(np.float64)
    h, w = crop.shape
    aspect = w / float(h)

    long_side = max(h, w)
    scale = FIT / float(long_side)
    out_h = max(1, int(round(h * scale)))
    out_w = max(1, int(round(w * scale)))
    small = _area_downsample(crop, out_h, out_w)

    cell = np.zeros((CELL, CELL), np.float64)
    # center-of-mass placement inside the CELL frame
    m = small.sum()
    if m > 0:
        cy = (small.sum(1) * np.arange(out_h)).sum() / m
        cx = (small.sum(0) * np.arange(out_w)).sum() / m
    else:
        cy, cx = (out_h - 1) / 2.0, (out_w - 1) / 2.0
    off_y = int(round(CELL / 2.0 - cy))
    off_x = int(round(CELL / 2.0 - cx))
    off_y = int(np.clip(off_y, 0, CELL - out_h))
    off_x = int(np.clip(off_x, 0, CELL - out_w))
    cell[off_y:off_y + out_h, off_x:off_x + out_w] = small

    if band is not None:
        top, bot = band
        span = max(1.0, float(bot - top))
        rel_y = float(((y0 + y1) / 2.0 - top) / span)
    else:
        rel_y = 0.5
    return Norm(cell.astype(np.float32), float(aspect), rel_y)
