"""Long-run linework removal — keep the lettering, drop the drawing.

Sheet borders, title-block rules, leaders and dimension lines are long,
axis-aligned ink runs; a character stroke is short.  Removing runs longer than
a few glyph-heights is equivalent to a morphological opening with a 1×L / L×1
structuring element (OCR_PLAN §2 step 6a) for solid lines, but expressed
directly on the run-length representation the component labeler already uses —
so it stays vectorized and needs no separate erode/dilate pass.

Honest scope (OCR_PLAN §8): only **axis-aligned** linework is touched.  Diagonal
linework and text *fused* into a line (dimension text on its dimension line,
labels welded to a bubble) are the explicit P1 SKIP — CC merges glyph+line into
one blob that no axis-aligned rule can cleave.  On mixed pages the app's own
vector line geometry can be subtracted first; ``strip_lines`` accepts that mask
hook but P1 callers may pass ``None``.
"""
from __future__ import annotations

import numpy as np

LINE_RUN_FACTOR = 3.0    # a run longer than this × glyph height is "linework"
MIN_LINE_LEN = 40        # absolute floor (px @300, OCR_PLAN §5 SE ≈ 40–50 px)


def _remove_long_runs(ink: np.ndarray, min_len: int) -> np.ndarray:
    """Zero out horizontal runs of length ≥ ``min_len`` (vectorized).

    Uses the same padded column-diff run extraction as ``components``; every
    qualifying run is blanked by scattering its pixels back to False.
    """
    H, W = ink.shape
    padded = np.zeros((H, W + 2), np.int8)
    padded[:, 1:W + 1] = ink.astype(np.int8)
    d = np.diff(padded, axis=1)
    starts = np.argwhere(d == 1)
    ends = np.argwhere(d == -1)
    if starts.size == 0:
        return ink
    row = starts[:, 0]
    c0 = starts[:, 1]
    c1 = ends[:, 1] - 1
    lengths = c1 - c0 + 1
    long = lengths >= min_len
    if not long.any():
        return ink
    out = ink.copy()
    lr, lc0, ll = row[long], c0[long], lengths[long]
    total = int(ll.sum())
    starts_off = np.repeat(np.cumsum(ll) - ll, ll)
    cols = np.repeat(lc0, ll) + (np.arange(total) - starts_off)
    rows = np.repeat(lr, ll)
    out[rows, cols] = False
    return out


def strip_lines(ink: np.ndarray, glyph_h: float, vector_line_mask=None):
    """Remove long horizontal + vertical runs; return the lettering-only ink.

    ``glyph_h`` sizes the length threshold (``max(LINE_RUN_FACTOR·glyph_h,
    MIN_LINE_LEN)``); vertical runs are handled by transposing.  If
    ``vector_line_mask`` is given (a boolean of known line pixels from the app's
    own vector geometry) it is cleared first.  A no-op when the sheet has no
    runs that long — exactly the clean-title-block case.
    """
    out = ink
    if vector_line_mask is not None:
        out = out & ~np.asarray(vector_line_mask, bool)
    min_len = int(max(LINE_RUN_FACTOR * glyph_h, MIN_LINE_LEN))
    out = _remove_long_runs(out, min_len)                 # horizontal
    out = _remove_long_runs(out.T, min_len).T             # vertical
    return out
