"""Run-based connected-component labeling + geometric glyph gates.

Connected-component labeling is the Tracer's bottleneck (OCR_PLAN §5 timing:
0.3–2 s on an ARCH-D sheet), so it is implemented **run-based and vectorized**
— there is no per-pixel Python loop anywhere.  A 70+ megapixel sheet is reduced
first to its horizontal ink *runs* (a few tens of thousands of them), and only
runs are unioned:

1. Extract every horizontal run in one vectorized pass (column-diff of a padded
   ink image → run starts/ends).
2. Two-pass union-find over runs: sweep adjacent row pairs with a two-pointer
   interval overlap (8-connected, so each run is dilated by one column), union
   the runs that touch.  The per-*row* loop is O(#rows), never O(#pixels).
3. Flatten the forest, relabel consecutively, and reduce every run into its
   component's bounding box + area with grouped ``np.minimum.at`` / ``add.at``.

``label`` returns an ``int32`` label image (0 = background) and a list of
``Box`` records.  ``filter_glyphs`` then applies the OCR_PLAN §5 geometric
gates that reject linework, hatch and speckle *without any training data*.

Coordinates: ``x`` is column, ``y`` is row; ``x0/y0/x1/y1`` are inclusive
pixel bounds, so ``w = x1 - x0 + 1`` and ``h = y1 - y0 + 1``.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np


class Box(NamedTuple):
    label: int
    x0: int
    y0: int
    x1: int
    y1: int
    area: int          # ink pixel count (not bbox area)

    @property
    def w(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def h(self) -> int:
        return self.y1 - self.y0 + 1

    @property
    def fill(self) -> float:
        return self.area / float(self.w * self.h)


# --- OCR_PLAN §5 despeckle / glyph gates (referenced to 300 dpi) ------------
MIN_AREA_300 = 8         # drop CCs smaller than this many px @300 dpi
MIN_SIDE = 3             # drop CCs whose bbox side < 3 px
CAP_LO, CAP_HI = 10, 60  # plausible cap-height band (px); wide for P1 safety
FILL_LO = 0.06           # below this the bbox is too sparse (hatch/line remnant)
SOLID_FILL = 0.88        # a *large* box this full is a filled block, not a glyph
SHEET_FRAC = 0.5         # drop any CC whose bbox side spans > 50% of the sheet
ELONG_RATIO = 4.0        # "longer side > 4× glyph height" half of the gate
ELONG_ASPECT = 8.0       # "... AND elongation > 8" — protects I 1 l - ' " /


def _extract_runs(ink: np.ndarray):
    """Return (row, c0, c1) arrays for every horizontal ink run (vectorized).

    ``c0/c1`` are inclusive column bounds.  A one-column False pad on each side
    turns run boundaries into +1/-1 transitions that ``argwhere`` reads out in
    row-major order, so the i-th start pairs with the i-th end.
    """
    H, W = ink.shape
    padded = np.zeros((H, W + 2), dtype=np.int8)
    padded[:, 1:W + 1] = ink.astype(np.int8)
    d = np.diff(padded, axis=1)              # shape (H, W+1)
    starts = np.argwhere(d == 1)             # (row, start_col)
    ends = np.argwhere(d == -1)              # (row, end_col+1)
    row = starts[:, 0].astype(np.int64)
    c0 = starts[:, 1].astype(np.int64)
    c1 = (ends[:, 1] - 1).astype(np.int64)
    return row, c0, c1


def _union_find_over_runs(row, c0, c1, H):
    """8-connected union-find over runs; returns per-run component ids (0..K-1).

    Runs are already grouped by row (argwhere is row-major).  For each adjacent
    row pair we two-pointer the two sorted run lists and union any that overlap
    once the upper run is dilated by a column (8-connectivity).
    """
    n = row.shape[0]
    parent = np.arange(n, dtype=np.int64)

    def find(a):
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:      # path compression
            parent[a], a = root, parent[a]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # index bounds of each row's runs within the row-sorted arrays
    starts_of_row = np.searchsorted(row, np.arange(H))
    starts_of_row = np.append(starts_of_row, n)
    for r in range(H - 1):
        a_lo, a_hi = starts_of_row[r], starts_of_row[r + 1]
        b_lo, b_hi = starts_of_row[r + 1], starts_of_row[r + 2]
        i, j = a_lo, b_lo
        while i < a_hi and j < b_hi:
            # dilate the upper run by one column for 8-connectivity
            if c1[i] + 1 < c0[j]:
                i += 1
            elif c1[j] + 1 < c0[i]:
                j += 1
            else:
                union(i, j)
                if c1[i] < c1[j]:
                    i += 1
                else:
                    j += 1
    roots = np.array([find(k) for k in range(n)], dtype=np.int64)
    _, comp = np.unique(roots, return_inverse=True)
    return comp.astype(np.int64)


def label(ink: np.ndarray):
    """Label 8-connected components; return ``(labels int32, [Box, ...])``.

    ``labels`` is 0 for background and ``component_id + 1`` for ink.  Boxes are
    sorted by label.  Empty input yields an all-zero image and no boxes.
    """
    ink = np.ascontiguousarray(ink.astype(bool))
    H, W = ink.shape
    labels = np.zeros((H, W), dtype=np.int32)
    row, c0, c1 = _extract_runs(ink)
    if row.size == 0:
        return labels, []
    comp = _union_find_over_runs(row, c0, c1, H)
    K = int(comp.max()) + 1

    # reduce runs → per-component bbox + ink area (all vectorized)
    x0 = np.full(K, W, np.int64)
    y0 = np.full(K, H, np.int64)
    x1 = np.full(K, -1, np.int64)
    y1 = np.full(K, -1, np.int64)
    area = np.zeros(K, np.int64)
    np.minimum.at(x0, comp, c0)
    np.maximum.at(x1, comp, c1)
    np.minimum.at(y0, comp, row)
    np.maximum.at(y1, comp, row)
    np.add.at(area, comp, c1 - c0 + 1)

    # paint the label image by scattering each run's pixels (vectorized, no
    # per-pixel Python loop): expand runs to (row, col) pairs via cumulative
    # offsets and assign component_id+1.
    lengths = (c1 - c0 + 1).astype(np.int64)
    total = int(lengths.sum())
    starts = np.repeat(np.cumsum(lengths) - lengths, lengths)
    within = np.arange(total) - starts
    cols = np.repeat(c0, lengths) + within
    rows = np.repeat(row, lengths)
    labels[rows, cols] = np.repeat(comp + 1, lengths).astype(np.int32)

    boxes = [Box(int(k + 1), int(x0[k]), int(y0[k]), int(x1[k]), int(y1[k]),
                 int(area[k])) for k in range(K)]
    return labels, boxes


def _median_glyph_h(boxes, dpi: int = 300) -> float:
    """Median height of the CAP-band boxes; robust seed for the size gates.

    When the CAP band captures only a small minority of the boxes — the
    signature of very large isolated lettering, where every letter exceeds the
    band ceiling and just a mark (a hyphen) falls inside — the median would
    track that mark and shrink ``glyph_h`` to the mark's own height, which then
    makes the mark itself look like an oversized solid block and drops it.  Fall
    back to the full height distribution in that case so the size gates stay
    glyph-scaled and thin marks survive (a P2 marks-reading fix).

    Sub-despeckle speckle is excluded FIRST.  A noisy scan floods the box set
    with thousands of 1–2 px salt-and-pepper components; a raw median over them
    collapses ``glyph_h`` toward the noise height, and the minority test then
    misfires (the real glyphs look like a minority against the speckle), which
    mis-scales every size gate — the elongation gate would then reject a real
    ``I`` as elongated linework and every thin glyph (``I 1 l - . ' "``) would
    vanish (measured: an 11% CER on a speckled photocopy was ENTIRELY these
    dropped thin glyphs).  The speckle excluded here is exactly what
    :func:`filter_glyphs` despeckles by area/side, so on a clean render (no
    sub-floor box) this is byte-identical and the glyph-height scale is measured
    only over glyph-sized ink."""
    scale2 = (dpi / 300.0) ** 2
    min_area = MIN_AREA_300 * scale2
    real = [b for b in boxes
            if b.area >= min_area and min(b.w, b.h) >= MIN_SIDE]
    if not real:                     # nothing above the floor: keep the old view
        real = list(boxes)
    hs = [b.h for b in real if CAP_LO <= b.h <= 3 * CAP_HI]
    if len(hs) < max(2, len(real) // 2):
        hs = [b.h for b in real]
    return float(np.median(hs)) if hs else 0.0


def filter_glyphs(boxes, glyph_h: float | None = None, dpi: int = 300):
    """Keep the boxes that plausibly bound a single character.

    Applies the OCR_PLAN §5 gates in order: despeckle (area/side), sheet-size
    reject, fill band, and the **elongation gate that rejects only when the
    longer side is > 4× glyph height AND elongation > 8** — a bare aspect-ratio
    cut would delete ``I 1 l - ' " /`` and is explicitly forbidden by the plan.
    Round baseline dots (tiny, near-square, high fill) are whitelisted.
    """
    if not boxes:
        return []
    scale2 = (dpi / 300.0) ** 2
    min_area = MIN_AREA_300 * scale2
    if glyph_h is None:
        glyph_h = _median_glyph_h(boxes, dpi=dpi)
    if glyph_h <= 0:
        glyph_h = CAP_LO
    # sheet extent from the bounding envelope of all ink
    sheet_w = max(b.x1 for b in boxes) - min(b.x0 for b in boxes) + 1
    sheet_h = max(b.y1 for b in boxes) - min(b.y0 for b in boxes) + 1
    big_side = SHEET_FRAC * max(sheet_w, sheet_h)
    kept = []
    for b in boxes:
        long_side = max(b.w, b.h)
        short_side = max(1, min(b.w, b.h))
        # despeckle: hard floors on ink area and bbox side (noise, not lettering)
        if b.area < min_area or min(b.w, b.h) < MIN_SIDE:
            continue
        # sheet-spanning frame / border
        if long_side > big_side:
            continue
        # oversize blob (linework fused, title-block rule, hatch clump)
        if long_side > ELONG_RATIO * glyph_h and (long_side / short_side) > ELONG_ASPECT:
            continue
        # too-sparse: a bbox this empty is a line/hatch remnant, not a letter
        if b.fill < FILL_LO:
            continue
        # solid-block reject: high fill is only linework when the box is also
        # *large* on both sides — small high-fill marks ( - . ' ) must survive.
        if (b.fill > SOLID_FILL and long_side >= glyph_h
                and short_side >= 0.5 * glyph_h):
            continue
        kept.append(b)
    return kept
