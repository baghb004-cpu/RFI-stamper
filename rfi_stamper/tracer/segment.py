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
  vertically — e.g. a dotted stem).  ``split_touching`` is the honest P2 stub:
  a run-of-glyphs wider than 1.3× the median is cut into equal pitch slices;
  the drop-fall valley split is deferred to P2 (OCR_PLAN §5 touching-CC).

All functions take/return the ``Box`` records from ``components`` and never
mutate their inputs.
"""
from __future__ import annotations

import numpy as np

LINE_BAND_FACTOR = 0.6   # new line when y-center gap > this × median height
WORD_GAP_FACTOR = 0.42   # new word when gap > this × median glyph *height*
TOUCH_WIDE_FACTOR = 1.3  # split trigger: box wider than this × median width


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
