"""Group glyph boxes into text lines, then words, then characters.

Reading order is rebuilt bottom-up from the filtered component boxes
(OCR_PLAN §2 step 7):

* ``group_lines`` clusters boxes into horizontal bands.  Uppercase technical
  lettering is a clean 2-line cap/baseline model, so boxes whose vertical
  centers fall within a fraction of the median glyph height belong to one line.
* ``group_words`` splits a line at **adaptive** gaps — a break when the space to
  the next glyph exceeds a fraction of the median character width (Wong's rule),
  which scales with the lettering instead of a fixed pixel count.
* P1 treats one component as one character.  ``merge_broken`` rejoins a glyph
  that a faint scan split into pieces (small horizontal union that also overlaps
  vertically — e.g. a dotted stem).  ``split_touching`` (the P1 equal-pitch
  stub) is kept; **P2 adds real touching-glyph handling** (OCR_PLAN §5/§7):
  ``split_glyph_boxes`` cuts a wide box that does not read as one glyph at
  vertical-projection valleys refined by a **drop-fall** {down, down-left,
  down-right} water path, over-segments, then keeps the **DP recombination**
  that maximizes total classifier confidence.

All functions take/return the ``Box`` records from ``components`` and never
mutate their inputs.
"""
from __future__ import annotations

import numpy as np

LINE_BAND_FACTOR = 0.6   # new line when y-center gap > this × median height
WORD_GAP_FACTOR = 0.42   # new word when gap > this × median glyph *height*
TOUCH_WIDE_FACTOR = 1.3  # split trigger: box wider than this × median width
SPLIT_WHOLE_CONF = 0.82  # a wide box reading this well as one glyph is not split
SEG_W_LO, SEG_W_HI = 0.34, 1.78   # a DP segment's width, in median widths


def _median(vals):
    return float(np.median(vals)) if len(vals) else 0.0


def group_lines(boxes):
    """Cluster boxes into text lines by vertical-center bands; return lines.

    Each line is a list of boxes left-to-right.  Lines are ordered top-to-bottom
    (smaller row first).  A robust median height sets the band tolerance so the
    split survives a stray tall or short glyph.
    """
    if not boxes:
        return []
    med_h = _median([b.h for b in boxes]) or 1.0
    tol = LINE_BAND_FACTOR * med_h
    by_y = sorted(boxes, key=lambda b: (b.y0 + b.y1) / 2.0)
    lines, cur = [], [by_y[0]]
    cy = (by_y[0].y0 + by_y[0].y1) / 2.0
    for b in by_y[1:]:
        c = (b.y0 + b.y1) / 2.0
        if c - cy <= tol:
            cur.append(b)
        else:
            lines.append(cur)
            cur = [b]
        cy = c
    lines.append(cur)
    for ln in lines:
        ln.sort(key=lambda b: b.x0)
    lines.sort(key=lambda ln: min(b.y0 for b in ln))
    return lines


def merge_broken(line):
    """Rejoin components a faint scan split; return a new left-to-right list.

    Two adjacent boxes merge only when their x-ranges **overlap** and their
    y-ranges overlap — the unambiguous signature of one glyph broken into
    pieces (a detached accent, a snapped stem bridge).  Properly-spaced
    characters in a word never overlap horizontally, so this can never fuse two
    real letters; that conservatism is deliberate for P1 (where CC = glyph and
    merges should be rare).  The heavier over-segment/DP recombination is P2.
    """
    if not line:
        return []
    order = sorted(line, key=lambda b: b.x0)
    out = [order[0]]
    from .components import Box
    for b in order[1:]:
        a = out[-1]
        x_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
        v_overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
        if x_overlap >= 0 and v_overlap > 0:
            out[-1] = Box(a.label, min(a.x0, b.x0), min(a.y0, b.y0),
                          max(a.x1, b.x1), max(a.y1, b.y1), a.area + b.area)
        else:
            out.append(b)
    return out


def split_touching(line):
    """P2 stub: equal-pitch cut of a too-wide box; return boxes with cut spans.

    Emits ``(box, x0, x1)`` slices so the caller can crop each character.  For a
    normal-width glyph the single slice is the box itself.  A box wider than
    ``TOUCH_WIDE_FACTOR`` × median is divided into ``round(width/pitch)`` equal
    columns (pitch = median width); the drop-fall valley refinement is P2.
    """
    if not line:
        return []
    med_w = _median([b.w for b in line]) or 1.0
    out = []
    for b in line:
        if b.w > TOUCH_WIDE_FACTOR * med_w and med_w > 0:
            n = max(1, int(round(b.w / med_w)))
            edges = np.linspace(b.x0, b.x1 + 1, n + 1).astype(int)
            for i in range(n):
                out.append((b, int(edges[i]), int(edges[i + 1]) - 1))
        else:
            out.append((b, b.x0, b.x1))
    return out


def _col_profile(crop: np.ndarray) -> np.ndarray:
    """Vertical ink projection (ink pixels per column), lightly smoothed."""
    p = crop.astype(np.float64).sum(axis=0)
    if p.size >= 3:
        p = np.convolve(p, np.array([0.25, 0.5, 0.25]), mode="same")
    return p


def _dropfall_cut(crop: np.ndarray, seed_col: int) -> int:
    """Refine a cut near ``seed_col`` with a drop-fall water path.

    Starting at the seeded column the path descends row by row choosing the
    least-ink move of {down, down-left, down-right}, so the cut skirts strokes
    rather than slicing through them.  Returns the column that the path crosses
    the least ink at (a single representative cut column for a rectangular
    split).  Pure numpy/stdlib, deterministic.
    """
    H, W = crop.shape
    ink = crop.astype(bool)
    col = int(np.clip(seed_col, 1, W - 2))
    crossed = 0
    cols = []
    for r in range(H):
        cols.append(col)
        if ink[r, col]:
            crossed += 1
        best = col
        best_ink = ink[min(r + 1, H - 1), col]
        for dc in (-1, 1):
            c2 = col + dc
            if 1 <= c2 <= W - 2:
                v = ink[min(r + 1, H - 1), c2]
                if int(v) < int(best_ink):
                    best_ink, best = v, c2
        col = best
    return int(np.median(cols)) if cols else seed_col


def candidate_cuts(crop: np.ndarray, med_w: float) -> list:
    """Over-segmentation cut columns: pitch guesses snapped to valleys.

    ``n = round(width/pitch)`` ideal boundaries, each searched within ±0.3×
    median width for the lowest projection column and refined by drop-fall; the
    strongest interior valleys are added so DP has room to recombine.
    """
    W = crop.shape[1]
    prof = _col_profile(crop)
    n = max(2, int(round(W / max(1.0, med_w))))
    win = max(2, int(round(0.3 * med_w)))
    cuts = set()
    for i in range(1, n):
        ideal = int(round(W * i / n))
        lo, hi = max(1, ideal - win), min(W - 1, ideal + win)
        local = lo + int(np.argmin(prof[lo:hi])) if hi > lo else ideal
        cuts.add(int(np.clip(_dropfall_cut(crop, local), 1, W - 1)))
    # add strong interior valleys (local minima below the mean profile)
    for c in range(2, W - 2):
        if prof[c] <= prof[c - 1] and prof[c] < prof[c + 1] \
                and prof[c] < 0.6 * prof.mean():
            cuts.add(c)
    return sorted(cuts)


def _score_segment(crop, x0, x1, med_w, clf, band_rel=0.5):
    """Classifier confidence of the sub-crop ``crop[:, x0:x1]`` (or −inf)."""
    from .normalize import norm_glyph
    w = x1 - x0
    if w < SEG_W_LO * med_w or w > SEG_W_HI * med_w:
        return -1e9, None
    sub = crop[:, x0:x1]
    if not sub.any():
        return -1e9, None
    ng = norm_glyph(sub)
    ranked = clf.classify(ng.cell, ng.aspect, band_rel)
    return float(ranked[0][1]), ranked[0][0]


def dp_recombine(crop, cuts, med_w, clf):
    """Choose the cut subset maximizing total confidence (DP over boundaries).

    ``best[j]`` = best total score to cover columns ``[0, B[j])``; a segment
    ``[B[i], B[j])`` contributes its classifier confidence when its width is
    glyph-plausible.  Returns the list of ``(x0, x1)`` column spans of the
    winning partition (⩾ 1 span).
    """
    W = crop.shape[1]
    B = [0] + [c for c in cuts if 0 < c < W] + [W]
    B = sorted(set(B))
    m = len(B)
    NEG = -1e18
    best = [NEG] * m
    prev = [-1] * m
    best[0] = 0.0
    for j in range(1, m):
        for i in range(j):
            sc, _ch = _score_segment(crop, B[i], B[j], med_w, clf)
            if best[i] + sc > best[j]:
                best[j] = best[i] + sc
                prev[j] = i
    # reconstruct
    spans = []
    j = m - 1
    if best[j] <= NEG / 2:      # nothing plausible: fall back to whole box
        return [(0, W)]
    while j > 0 and prev[j] >= 0:
        i = prev[j]
        spans.append((B[i], B[j]))
        j = i
    spans.reverse()
    return spans or [(0, W)]


def split_glyph_boxes(ink, box, med_w: float, clf):
    """Absolute ``(y0,x0,y1,x1)`` sub-boxes for one component box.

    A box no wider than ``TOUCH_WIDE_FACTOR`` × median, or one that already
    reads as a single glyph with confidence ≥ ``SPLIT_WHOLE_CONF`` (so wide
    single glyphs M, W, 0 are never sliced), is returned whole.  Otherwise it is
    over-segmented (drop-fall valleys) and the DP-recombined partition is
    returned — each span mapped back to absolute image coordinates and cropped
    to its own ink rows.
    """
    y0, x0, y1, x1 = box.y0, box.x0, box.y1, box.x1
    whole = [(y0, x0, y1, x1)]
    if box.w <= TOUCH_WIDE_FACTOR * med_w or med_w <= 0:
        return whole
    crop = ink[y0:y1 + 1, x0:x1 + 1]
    from .normalize import norm_glyph
    ng = norm_glyph(crop)
    if clf.classify(ng.cell, ng.aspect, 0.5)[0][1] >= SPLIT_WHOLE_CONF:
        return whole
    cuts = candidate_cuts(crop, med_w)
    spans = dp_recombine(crop, cuts, med_w, clf)
    if len(spans) <= 1:
        return whole
    out = []
    for (a, b) in spans:
        sub = crop[:, a:b]
        rows = np.where(sub.any(axis=1))[0]
        if rows.size == 0:
            continue
        out.append((y0 + int(rows.min()), x0 + a,
                    y0 + int(rows.max()), x0 + b - 1))
    return out or whole


def group_words(line):
    """Split one line into words at adaptive gaps; return lists of boxes.

    Gap = next box's left minus current box's right.  The threshold scales with
    the median glyph **height** (cap height in the uppercase 2-line model), not
    the width: character widths swing 3:1 (``I`` vs ``M``) so a width-based
    scale is dragged down by narrow glyphs and shreds large lettering, whereas a
    space is a stable ≈0.3–0.5 em ≈ fraction-of-cap-height regardless of which
    letters flank it.  A break opens when the gap exceeds ``WORD_GAP_FACTOR`` ×
    median height, so wide inter-word spaces separate while tight inter-character
    spacing (and hyphens) stay joined at any font size.
    """
    if not line:
        return []
    order = sorted(line, key=lambda b: b.x0)
    med_h = _median([b.h for b in order]) or 1.0
    thresh = WORD_GAP_FACTOR * med_h
    words, cur = [], [order[0]]
    for b in order[1:]:
        gap = b.x0 - cur[-1].x1 - 1
        if gap > thresh:
            words.append(cur)
            cur = [b]
        else:
            cur.append(b)
    words.append(cur)
    return words
