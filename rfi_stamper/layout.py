"""Note text, box layout math, and the measured-empty rectangle finder."""
from __future__ import annotations

import math
import re

import numpy as np

# From-scratch Core-14 metrics — proven equal to the reportlab oracle to
# machine epsilon (tests/test_minipdf.py), so box geometry and the header
# width-fit are byte-identical to the historical reportlab-measured layout.
from .minipdf.metrics import string_width as stringWidth
# ONE greedy word-wrap for the whole app (same metrics, same algorithm as the
# flow engine; behavior guarded by the stamp/verify + oracle-parity suites)
from .minipdf.flow import wrap_text as wrap  # noqa: E402

RED = (0.84, 0.06, 0.06)
F_HDR, S_HDR, L_HDR = "Helvetica-Bold", 9.2, 11.6
F_BOD, S_BOD, L_BOD = "Helvetica", 7.7, 9.5
PAD, GAP, BORDER = 10.0, 7.5, 1.2

DARK_THRESH = 225      # gray level below which a pixel counts as content
DIFF_THRESH = 25       # gray delta that counts as a rendered change
PAD_PX = 9             # clear margin (px) verified around every box
SEARCH_PAD = PAD_PX + 3  # extra slack while searching; absorbs px rounding
STEP = 8               # placement scan stride (px)

# search zones as page fractions (x0, y0, x1, y1, corner) -- y measured UP.
ZONES = [
    (0.035, 0.035, 0.37, 0.50, "bl"),   # lower-left (preferred)
    (0.28, 0.035, 0.80, 0.24, "bl"),    # bottom-center strip
    (0.035, 0.58, 0.36, 0.965, "tl"),   # upper-left
    (0.035, 0.28, 0.38, 0.78, "bl"),    # mid-left
    (0.55, 0.035, 0.88, 0.32, "bl"),    # lower-right (left of typical title block)
    (0.035, 0.035, 0.965, 0.965, "bl"), # anywhere
]


def clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    dot = cut.rfind(". ")
    if dot > n * 0.45:
        return cut[:dot + 1]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).rstrip(",;") + " \u2026"


def note_body(rec, summarizer=None) -> str:
    if summarizer is not None:
        try:
            out = summarizer.summarize(rec)
            if out and 20 <= len(out) <= 320:
                return out
        except Exception:   # noqa: BLE001 -- summarizer is best-effort, never fatal
            pass
    q = clip(rec.question, 175) or "(question text not readable \u2014 see RFI document)"
    a = f"A: {clip(rec.answer, 150)}" if rec.has_answer else "Resp: not in file."
    return f"Q: {q} {a}"


def make_entries(records, summarizer=None, statuses: dict | None = None):
    out = []
    for r in records:
        hdr = f"RFI {r.number} \u2014 {clip(r.title, 46).upper()}"
        if statuses:
            from . import resolution   # lazy import; avoids an import cycle
            # append AFTER clipping so the status suffix is never clipped away
            hdr += resolution.status_suffix(statuses.get(r.number, ""))
        out.append((r.number, hdr, note_body(r, summarizer)))
    return out


def _fit_header(hdr: str, inner: float) -> str:
    """Shrink an over-wide single-line header to fit `inner` pt, trimming the
    TITLE while keeping the ``RFI NNN — `` prefix and any ``· STATUS``
    suffix intact.  The status suffix is USER-APPROVED to never be clipped, so
    it is split off and re-appended after the title is trimmed.  Header height
    is one line regardless, so this never changes box geometry -- it only stops
    a long title from printing past the right border (which would land red text
    over linework and fail verification)."""
    if stringWidth(hdr, F_HDR, S_HDR) <= inner:
        return hdr
    sep = " · "                       # status_suffix separator (middle dot)
    idx = hdr.rfind(sep)
    base, suffix = (hdr[:idx], hdr[idx:]) if idx != -1 else (hdr, "")
    dash = base.find(" — ")           # keep 'RFI NNN — '
    keep = dash + 3 if dash != -1 else 0
    ell = "…"
    hi = len(base)
    while hi > keep and stringWidth(base[:hi].rstrip() + ell + suffix,
                                    F_HDR, S_HDR) > inner:
        hi -= 1
    return base[:hi].rstrip() + ell + suffix


def layout_entries(entries, w):
    """-> (height_pt, [(hdr, body_lines)]) for a box of width w."""
    inner = w - 2 * PAD
    items, h = [], PAD
    for _num, hdr, body in entries:
        hdr = _fit_header(hdr, inner)
        blines = wrap(body, F_BOD, S_BOD, inner)
        items.append((hdr, blines))
        h += L_HDR + len(blines) * L_BOD + GAP
    return h + PAD - GAP, items


def pack(entries, w, max_h):
    """Split entries into chunks whose boxes each fit max_h."""
    chunks, cur = [], []
    for e in entries:
        if cur and layout_entries(cur + [e], w)[0] > max_h:
            chunks.append(cur)
            cur = []
        cur.append(e)
    if cur:
        chunks.append(cur)
    return chunks


# ------------------------------------------------------------- placement ---

def integral(gray: np.ndarray) -> np.ndarray:
    dark = (gray < DARK_THRESH).astype(np.int64)
    ii = np.zeros((dark.shape[0] + 1, dark.shape[1] + 1), dtype=np.int64)
    ii[1:, 1:] = dark.cumsum(0).cumsum(1)
    return ii


def _rect_sum(ii, x0, y0, x1, y1) -> int:
    return int(ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0])


def find_spot(ii, img_w, img_h, w_pt, h_pt, scale, occupied):
    """Find (x_pt, ytop_pt) of a fully-empty padded window; None if impossible.
    `occupied` = list of (x0,y0,x1,y1) px rects already claimed on this page."""
    w_px = int(w_pt * scale) + 2 * SEARCH_PAD
    h_px = int(h_pt * scale) + 2 * SEARCH_PAD

    def blocked(x, y):
        for (ox0, oy0, ox1, oy1) in occupied:
            if x < ox1 and x + w_px > ox0 and y < oy1 and y + h_px > oy0:
                return True
        return False

    for (fx0, fy0, fx1, fy1, corner) in ZONES:
        X0, X1 = int(fx0 * img_w), int(fx1 * img_w) - w_px
        Yt = int((1 - fy1) * img_h)                # zone top in image rows
        Yb = int((1 - fy0) * img_h) - h_px         # lowest allowed top row
        if X1 <= X0 or Yb <= Yt:
            continue
        # Include the far endpoints so a clear window that begins between grid
        # lines (or hard against the zone edge) is still found instead of being
        # spilled to the appendix.  Endpoints only ADD candidates.
        xs = list(range(X0, X1, STEP))
        if not xs or xs[-1] != X1:
            xs.append(X1)
        if corner == "bl":
            ys = list(range(Yb, Yt, -STEP))
            if not ys or ys[-1] != Yt:
                ys.append(Yt)
        else:
            ys = list(range(Yt, Yb, STEP))
            if not ys or ys[-1] != Yb:
                ys.append(Yb)
        for y in ys:
            for x in xs:
                if blocked(x, y):
                    continue
                if _rect_sum(ii, x, y, x + w_px, y + h_px) == 0:
                    x_pt = (x + SEARCH_PAD) / scale
                    ytop_pt = (img_h - (y + SEARCH_PAD)) / scale
                    return round(x_pt, 1), round(ytop_pt, 1), (x, y, x + w_px, y + h_px)
    return None


def find_spot_near(ii, img_w, img_h, w_pt, h_pt, scale, occupied, anchor_px,
                   reach_px, pad_px=3, step=4, inside=None):
    """Find a fully-empty padded window NEAR an anchor; None if impossible.

    The same measured-empty rule as :func:`find_spot` -- the padded window
    must contain ZERO content pixels in the integral image ``ii`` and miss
    every ``occupied`` rect, no exceptions -- but searched outward from
    ``anchor_px`` = (x, y) in image pixels instead of across the fixed
    zones: candidate windows on a ``step`` px lattice are tried
    nearest-first, by the distance from the anchor to the window's nearest
    edge point, out to ``reach_px``.  ``inside`` = (x0, y0, x1, y1) px
    keeps the whole padded window within that rect.  Ties break top-down,
    then left to right, so the answer is deterministic.

    Returns ``(x_pt, ytop_pt, (x0, y0, x1, y1) px)`` in :func:`find_spot`'s
    convention: the box's left and TOP edge in page points (origin
    bottom-left) and the padded pixel window it claimed."""
    pad_px = int(pad_px)
    step = max(1, int(step))
    w_px = int(math.ceil(w_pt * scale)) + 2 * pad_px
    h_px = int(math.ceil(h_pt * scale)) + 2 * pad_px
    ax, ay = float(anchor_px[0]), float(anchor_px[1])
    lo_x, lo_y, hi_x, hi_y = 0, 0, int(img_w) - w_px, int(img_h) - h_px
    if inside is not None:
        lo_x = max(lo_x, int(math.ceil(inside[0])))
        lo_y = max(lo_y, int(math.ceil(inside[1])))
        hi_x = min(hi_x, int(math.floor(inside[2])) - w_px)
        hi_y = min(hi_y, int(math.floor(inside[3])) - h_px)
    # only windows that can come within reach of the anchor
    lo_x = max(lo_x, int(math.floor(ax - reach_px)) - w_px)
    lo_y = max(lo_y, int(math.floor(ay - reach_px)) - h_px)
    hi_x = min(hi_x, int(math.ceil(ax + reach_px)))
    hi_y = min(hi_y, int(math.ceil(ay + reach_px)))
    if hi_x < lo_x or hi_y < lo_y:
        return None
    xs = np.arange(lo_x, hi_x + 1, step, dtype=np.int64)
    ys = np.arange(lo_y, hi_y + 1, step, dtype=np.int64)
    if xs[-1] != hi_x:
        xs = np.append(xs, hi_x)        # the far edges only ADD candidates
    if ys[-1] != hi_y:
        ys = np.append(ys, hi_y)
    gx, gy = np.meshgrid(xs, ys)
    gx, gy = gx.ravel(), gy.ravel()
    dx = np.maximum(np.maximum(gx - ax, ax - (gx + w_px)), 0.0)
    dy = np.maximum(np.maximum(gy - ay, ay - (gy + h_px)), 0.0)
    dist = np.hypot(dx, dy)
    keep = dist <= float(reach_px)
    if not keep.any():
        return None
    gx, gy, dist = gx[keep], gy[keep], dist[keep]
    ink = (ii[gy + h_px, gx + w_px] - ii[gy, gx + w_px]
           - ii[gy + h_px, gx] + ii[gy, gx])
    ok = ink == 0
    for (ox0, oy0, ox1, oy1) in occupied:
        ok &= ~((gx < ox1) & (gx + w_px > ox0) & (gy < oy1) & (gy + h_px > oy0))
    if not ok.any():
        return None
    gx, gy, dist = gx[ok], gy[ok], dist[ok]
    k = np.lexsort((gx, gy, np.round(dist, 6)))[0]
    x, y = int(gx[k]), int(gy[k])
    x_pt = (x + pad_px) / scale
    ytop_pt = (img_h - (y + pad_px)) / scale
    return round(x_pt, 1), round(ytop_pt, 1), (x, y, x + w_px, y + h_px)
