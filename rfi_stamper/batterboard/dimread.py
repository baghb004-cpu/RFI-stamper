"""Dimension strings: each printed dimension paired with its line.

A dimension text sits centered just off the middle of its dimension line,
so the nearest visible segment whose MIDDLE band contains the text's
projection is the line the string measures (the Story Pole's pairing law:
ticks and extension lines sit near the ends, so the band test alone
rejects them).  Unlike the Story Pole, which only needs the length, the
Batterboard needs the two ENDS: the grid check and the architect string
check both ask what each end lands on.

Text is read through ``sightline.words`` and linework through the
Sightline's visible strokes, so a dimension whose line is not on the
plotted sheet is not a dimension.  Viewer points throughout.
"""
from __future__ import annotations

import math

from ..draft import parse_ftin

MIN_SEG_PT = 4.0
MID_BAND = (0.18, 0.82)
MIN_VALUE_FT = 1.0 / 12.0


def ftin_tokens(words) -> list:
    """Dimension-string candidates -> ``[(text, value_ft, rect)]``.
    Joins up to three adjacent words on a line (``12'-4`` + ``1/2"``) and
    keeps the longest parse per start; a candidate must carry a foot mark
    (bare numbers are far too common on a drawing)."""
    lines: dict = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    out = []
    for ws in lines.values():
        ws.sort(key=lambda w: w[7])
        for i in range(len(ws)):
            if i > 0 and ws[i - 1][4].strip() == "=":
                continue                      # the "= 1'-0\"" tail of a scale note
            best = None
            joined = ""
            x0, y0, x1, y1 = ws[i][:4]
            for k in range(i, min(i + 3, len(ws))):
                joined = (joined + " " + ws[k][4]).strip()
                x0, y0 = min(x0, ws[k][0]), min(y0, ws[k][1])
                x1, y1 = max(x1, ws[k][2]), max(y1, ws[k][3])
                if "'" not in joined and "’" not in joined:
                    continue
                v = parse_ftin(joined)
                if v is not None and v >= MIN_VALUE_FT:
                    best = (joined, v, (x0, y0, x1, y1))
            if best:
                out.append(best)
    keep = []
    for t in out:
        r = t[2]
        if not any(o is not t and len(o[0]) > len(t[0])
                   and o[2][0] <= r[0] + 0.01 and o[2][1] <= r[1] + 0.01
                   and o[2][2] >= r[2] - 0.01 and o[2][3] >= r[3] - 0.01 for o in out):
            keep.append(t)
    return keep


def line_segments(strokes, min_len: float = MIN_SEG_PT) -> list:
    """Visible straight stroke pieces -> ``[(a, b, length)]``."""
    segs = []
    for st in strokes:
        if st.curve is not None or len(st.pts) != 2:
            continue
        a, b = st.pts
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln >= min_len:
            segs.append((a, b, ln))
    return segs


def dimension_strings(words, strokes) -> list:
    """``[{"text", "value_ft", "a", "b", "length_pt", "rect"}]``: every
    dimension string with the two ends of its dimension line."""
    segs = line_segments(strokes)
    out = []
    for text, value, rect in ftin_tokens(words):
        cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
        reach = 3.0 * max(4.0, min(rect[2] - rect[0], rect[3] - rect[1]))
        best = None
        for a, b, ln in segs:
            ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
            proj = (cx - a[0]) * ux + (cy - a[1]) * uy
            if not (MID_BAND[0] * ln <= proj <= MID_BAND[1] * ln):
                continue
            perp = abs((cx - a[0]) * -uy + (cy - a[1]) * ux)
            if perp <= reach and (best is None or perp < best[0]):
                best = (perp, a, b, ln)
        if best is None:
            continue
        out.append({"text": text, "value_ft": value, "a": best[1], "b": best[2],
                    "length_pt": best[3], "rect": rect})
    return out
