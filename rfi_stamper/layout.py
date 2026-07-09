"""Note text, box layout math, and the measured-empty rectangle finder."""
from __future__ import annotations

import re

import numpy as np

# From-scratch Core-14 metrics — proven equal to the reportlab oracle to
# machine epsilon (tests/test_minipdf.py), so box geometry and the header
# width-fit are byte-identical to the historical reportlab-measured layout.
from .minipdf.metrics import string_width as stringWidth

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


def wrap(text, font, size, width):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if stringWidth(trial, font, size) <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


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
