"""The Slipsheet — vector drawing-revision diff (addendum redline).

Compares two revisions of a plan sheet the way a reviewer slip-sheets two
vellums on a light table, but on the VECTORS: what linework was added,
what was removed, where — clustered into change regions ("a moved wall is
one change, not 40 segments") — plus a word layer, because text is
invisible to vector extraction and a changed dimension value is the most
damaging silent miss a compare can make.

The whole diff is 1-D interval algebra per infinite line: segments from
both revisions land in (theta, rho) line buckets, each bucket's segments
become intervals along the line, collinear chains merge (gap <= GAP_TOL),
and added/removed are pure interval differences.  Exact matches, splits,
merges, extensions and partial erasures all fall out of ONE code path —
the classic false diff (a line re-exported as two touching pieces) merges
back and diffs to nothing by construction.

Registration is the whole game: revisions are routinely re-plotted with a
sub-point shift; align.auto_align's (dx, dy, rotation) is applied to the
revision's geometry first (when the score is trustworthy), or a caller-
supplied AlignResult is honored.

Honesty about scope: a vector diff sees strokes and words, not meaning.
Raster content needs align.py's pixel compare; bezier re-parameterization
and dash-phase churn can leave sliver noise (MIN_DIFF_PT eats most of
it).  The report says what was applied and what was capped.

All engine work happens in viewer page points (top-left origin, y down).
Pure fitz + numpy + stdlib + minipdf; GUI-free; offline.
"""
from __future__ import annotations

import math
import unicodedata

import fitz
import numpy as np

from .extrude import extract_segments

#: endpoint/interval quantum — matches extrude._QUANT_PT; exists for
#: producer float jitter and align rounding, not sloppy geometry.
TOL_PT = 0.5
#: line-bucket angular quantum, degrees.
THETA_TOL_DEG = 0.15
#: line-bucket offset quantum, pt (rho measured from page CENTER so the
#: magnitudes stay small).
RHO_TOL = 0.5
#: max gap bridged when merging collinear intervals into chains — kills
#: the split/merge false diff; deliberately below dash gaps (~3-6 pt) so
#: dashed linetypes stay dashed.
GAP_TOL = 0.75
#: diff intervals shorter than this are rounding slivers — dropped.
MIN_DIFF_PT = 2.0
#: region clustering grid (1/3 inch).
CLUSTER_CELL = 24.0
#: alignment is applied only when auto_align's confidence clears this.
ALIGN_MIN_SCORE = 0.35
#: segment cap per side (extrude's 4000 default is too low for dense
#: sheets; a one-sided cap MANUFACTURES diffs on the other side — warn).
MAX_SEGMENTS = 20000

_EPS = 1e-9


# ---------------------------------------------------------------- helpers ---

class _UF:
    """40-line union-find over hashable keys."""

    def __init__(self):
        self.p: dict = {}

    def find(self, k):
        p = self.p.setdefault(k, k)
        if p != k:
            p = self.p[k] = self.find(p)
        return p

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _page_info(pdf_path, page_no):
    doc = fitz.open(pdf_path)
    try:
        r = doc[int(page_no) - 1].rect
        return float(r.width), float(r.height)
    finally:
        doc.close()


def _apply_align(pts, align, ctr):
    """Map revision points onto the base: rotate about the page center,
    then shift — the same prerotation-then-shift order align.py renders
    with.  pts is (N, 2); returns a transformed copy."""
    th = math.radians(float(align.rotation or 0.0))
    c, s = math.cos(th), math.sin(th)
    v = pts - ctr
    out = np.empty_like(v)
    out[:, 0] = c * v[:, 0] - s * v[:, 1]
    out[:, 1] = s * v[:, 0] + c * v[:, 1]
    return out + ctr + (float(align.dx), float(align.dy))


def _merge_intervals(iv, gap):
    """Merge sorted-or-not (t0, t1) intervals, bridging gaps <= gap."""
    iv = sorted(iv)
    out = []
    for t0, t1 in iv:
        if out and t0 <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], t1)
        else:
            out.append([t0, t1])
    return [(a, b) for a, b in out]


def _subtract_intervals(a, b):
    """Pieces of interval set a not covered by interval set b (both
    merged + sorted)."""
    out = []
    for t0, t1 in a:
        cur = t0
        for u0, u1 in b:
            if u1 <= cur:
                continue
            if u0 >= t1:
                break
            if u0 > cur:
                out.append((cur, min(u0, t1)))
            cur = max(cur, u1)
            if cur >= t1:
                break
        if cur < t1:
            out.append((cur, t1))
    return out


def _norm_word(s: str) -> str:
    s = unicodedata.normalize("NFC", str(s))
    return "".join(" " if ch in "   " else ch for ch in s)


def _words(pdf_path, page_no):
    """[(text, (x0, y0, x1, y1))] in viewer page points.  get_text may
    return unrotated media coords on /Rotate pages (the sheets.py trap) —
    transform through page.rotation_matrix, never bounds-check."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[int(page_no) - 1]
        m = page.rotation_matrix if page.rotation % 360 else None
        out = []
        for w in page.get_text("words"):
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
            if m is not None:
                xs, ys = [], []
                for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                    xs.append(px * m.a + py * m.c + m.e)
                    ys.append(px * m.b + py * m.d + m.f)
                x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            text = _norm_word(text)
            if text.strip():
                out.append((text, (float(x0), float(y0),
                                   float(x1), float(y1))))
        return out
    finally:
        doc.close()


# ------------------------------------------------------------------- diff ---

def diff_pages(base_pdf, rev_pdf, base_page: int = 1, rev_page: int = 1,
               *, align="auto", log=print) -> dict:
    """Diff one page pair.  ``align``: "auto" runs align.auto_align and
    applies it when trustworthy; an AlignResult applies as given; None
    diffs unaligned.  Raises ValueError on raster-only pages (surfaced
    from extract_segments — the honest failure).

    Returns the DiffReport dict: regions (bbox, counts, lengths,
    has_text_change; sorted by change magnitude, numbered from 1),
    added/removed/base segment lists for rendering, word diffs, totals,
    the alignment actually used, and warnings.
    """
    warnings: list = []
    wa, ha = _page_info(base_pdf, base_page)
    wb, hb = _page_info(rev_pdf, rev_page)
    if abs(wa - wb) > 1.0 or abs(ha - hb) > 1.0:
        warnings.append(
            f"page sizes differ ({wa:.0f}x{ha:.0f} vs {wb:.0f}x{hb:.0f} pt)"
            " — re-plotted at another size? No scale recovery attempted.")

    old = extract_segments(base_pdf, base_page, min_len_pt=1.5,
                           max_segments=MAX_SEGMENTS, log=log)
    new = extract_segments(rev_pdf, rev_page, min_len_pt=1.5,
                           max_segments=MAX_SEGMENTS, log=log)
    if len(old) >= MAX_SEGMENTS or len(new) >= MAX_SEGMENTS:
        warnings.append(
            f"segment cap ({MAX_SEGMENTS}) hit — a one-sided cap can "
            "manufacture diffs; treat the report as partial.")
    A = np.array([[s.a[0], s.a[1], s.b[0], s.b[1]] for s in old], float)
    B = np.array([[s.a[0], s.a[1], s.b[0], s.b[1]] for s in new], float)

    # ---- registration ---------------------------------------------------
    align_used = None
    if align == "auto":
        from .align import auto_align
        align = auto_align(base_pdf, rev_pdf, base_page, rev_page)
        if align.score < ALIGN_MIN_SCORE:
            if abs(align.dx) > 2 or abs(align.dy) > 2 or align.rotation:
                warnings.append(
                    f"alignment confidence low (score {align.score:.2f}) — "
                    "sheets may not correspond; diffed unaligned.")
            align = None
    if align is not None and (abs(align.dx) > 0.4 or abs(align.dy) > 0.4
                              or align.rotation):
        ctr = np.array([wb / 2.0, hb / 2.0])
        B[:, 0:2] = _apply_align(B[:, 0:2], align, ctr)
        B[:, 2:4] = _apply_align(B[:, 2:4], align, ctr)
        align_used = {"dx": float(align.dx), "dy": float(align.dy),
                      "rotation": float(align.rotation),
                      "score": float(align.score)}

    # ---- line buckets: (theta, rho) cells + 3x3 union-find --------------
    segs = np.vstack([A, B])
    n_old = len(A)
    d = segs[:, 2:4] - segs[:, 0:2]
    ln = np.hypot(d[:, 0], d[:, 1])
    ln = np.where(ln < _EPS, _EPS, ln)
    u = d / ln[:, None]
    theta = np.arctan2(u[:, 1], u[:, 0])
    flip = theta < 0
    theta = np.where(flip, theta + math.pi, theta)
    theta = np.where(theta >= math.pi - 1e-12, theta - math.pi, theta)
    u = np.where(flip[:, None], -u, u)
    nrm = np.column_stack([-u[:, 1], u[:, 0]])
    ctr_a = np.array([wa / 2.0, ha / 2.0])
    mid = (segs[:, 0:2] + segs[:, 2:4]) / 2.0 - ctr_a
    rho = nrm[:, 0] * mid[:, 0] + nrm[:, 1] * mid[:, 1]

    th_tol = math.radians(THETA_TOL_DEG)
    nbins = int(math.ceil(math.pi / th_tol))
    tb = np.minimum((theta / th_tol).astype(int), nbins - 1)
    rb = np.floor(rho / RHO_TOL).astype(int)

    cells: dict = {}
    for i in range(len(segs)):
        cells.setdefault((int(tb[i]), int(rb[i])), []).append(i)
    uf = _UF()
    for (t, r) in cells:
        for dt in (-1, 0, 1):
            for dr in (-1, 0, 1):
                q = (t + dt, r + dr)
                if q in cells:
                    uf.union((t, r), q)
        if t <= 0:              # theta = 0/pi seam: direction flips, rho
            for dt in (-1, 0):  # negates — probe the wrapped cells
                for dr in (-1, 0, 1):
                    q = (nbins - 1 + dt, -r - 1 + dr)
                    if q in cells:
                        uf.union((t, r), q)

    groups: dict = {}
    for cell, idxs in cells.items():
        groups.setdefault(uf.find(cell), []).extend(idxs)

    # ---- per group: project, chain-merge, interval-diff ------------------
    added_iv, removed_iv = [], []       # (x0, y0, x1, y1) world pieces
    tot_add = tot_rem = 0.0
    for idxs in groups.values():
        idxs = sorted(idxs)
        k = max(idxs, key=lambda i: ln[i])          # longest member leads
        us, ns = u[k], nrm[k]
        # sign-align rho of members against the leader (seam groups mix)
        rhb = float(nrm[k, 0] * mid[k, 0] + nrm[k, 1] * mid[k, 1])
        olds, news = [], []
        for i in idxs:
            p0 = segs[i, 0:2] - ctr_a
            p1 = segs[i, 2:4] - ctr_a
            t0 = float(us[0] * p0[0] + us[1] * p0[1])
            t1 = float(us[0] * p1[0] + us[1] * p1[1])
            (olds if i < n_old else news).append(
                (min(t0, t1), max(t0, t1)))
        mo = _merge_intervals(olds, GAP_TOL)
        mn = _merge_intervals(news, GAP_TOL)

        def _emit(pieces, sink):
            n = 0.0
            for t0, t1 in pieces:
                if t1 - t0 < MIN_DIFF_PT:
                    continue
                p = ctr_a + np.outer([t0, t1], us) + rhb * ns
                sink.append((float(p[0, 0]), float(p[0, 1]),
                             float(p[1, 0]), float(p[1, 1])))
                n += t1 - t0
            return n

        tot_add += _emit(_subtract_intervals(mn, mo), added_iv)
        tot_rem += _emit(_subtract_intervals(mo, mn), removed_iv)

    # ---- word layer (text is invisible to get_drawings) ------------------
    from collections import Counter
    wo = _words(base_pdf, base_page)
    wn = _words(rev_pdf, rev_page)
    if align_used is not None:
        ctr = np.array([wb / 2.0, hb / 2.0])
        moved = []
        for text, (x0, y0, x1, y1) in wn:
            p = _apply_align(np.array([[x0, y0], [x1, y1]]), align, ctr)
            moved.append((text, (float(p[:, 0].min()), float(p[:, 1].min()),
                                 float(p[:, 0].max()), float(p[:, 1].max()))))
        wn = moved

    def _wkey(w):
        text, (x0, y0, _x1, _y1) = w
        return (text, round(x0 / 2.0), round(y0 / 2.0))

    co, cn = Counter(map(_wkey, wo)), Counter(map(_wkey, wn))
    by_key_o = {}
    for w in wo:
        by_key_o.setdefault(_wkey(w), []).append(w)
    by_key_n = {}
    for w in wn:
        by_key_n.setdefault(_wkey(w), []).append(w)
    words_removed = [by_key_o[k][0] for k in sorted(co - cn)]
    words_added = [by_key_n[k][0] for k in sorted(cn - co)]

    # ---- cluster change regions ------------------------------------------
    pieces = ([("seg+", b) for b in added_iv]
              + [("seg-", b) for b in removed_iv]
              + [("txt+", b) for _t, b in words_added]
              + [("txt-", b) for _t, b in words_removed])
    ruf = _UF()
    cell_of: dict = {}
    for pi, (_kind, (x0, y0, x1, y1)) in enumerate(pieces):
        cx0 = int(min(x0, x1) // CLUSTER_CELL)
        cx1 = int(max(x0, x1) // CLUSTER_CELL)
        cy0 = int(min(y0, y1) // CLUSTER_CELL)
        cy1 = int(max(y0, y1) // CLUSTER_CELL)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                key = (cx, cy)
                if key in cell_of:
                    ruf.union(("p", cell_of[key]), ("p", pi))
                else:
                    cell_of[key] = pi
                    ruf.union(("p", pi), ("p", pi))
    for (cx, cy), pi in list(cell_of.items()):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                q = (cx + dx, cy + dy)
                if q in cell_of:
                    ruf.union(("p", pi), ("p", cell_of[q]))

    clusters: dict = {}
    for pi in range(len(pieces)):
        clusters.setdefault(ruf.find(("p", pi)), []).append(pi)
    regions = []
    for members in clusters.values():
        xs, ys = [], []
        n_add = n_rem = 0
        add_len = rem_len = 0.0
        text_change = False
        for pi in members:
            kind, (x0, y0, x1, y1) = pieces[pi]
            xs += [x0, x1]
            ys += [y0, y1]
            if kind == "seg+":
                n_add += 1
                add_len += math.hypot(x1 - x0, y1 - y0)
            elif kind == "seg-":
                n_rem += 1
                rem_len += math.hypot(x1 - x0, y1 - y0)
            else:
                text_change = True
        regions.append({
            "bbox": (min(xs) - 6.0, min(ys) - 6.0,
                     max(xs) + 6.0, max(ys) + 6.0),
            "n_added": n_add, "n_removed": n_rem,
            "added_len_pt": round(add_len, 2),
            "removed_len_pt": round(rem_len, 2),
            "has_text_change": text_change,
        })
    regions.sort(key=lambda r: -(r["added_len_pt"] + r["removed_len_pt"]
                                 + (1.0 if r["has_text_change"] else 0.0)))

    return {
        "page_size": (wa, ha),
        "regions": regions,
        "added": added_iv,
        "removed": removed_iv,
        "base_segments": [(float(s.a[0]), float(s.a[1]),
                           float(s.b[0]), float(s.b[1])) for s in old],
        "words_added": words_added,
        "words_removed": words_removed,
        "totals": {"added": len(added_iv), "removed": len(removed_iv),
                   "added_len_pt": round(tot_add, 2),
                   "removed_len_pt": round(tot_rem, 2),
                   "words_added": len(words_added),
                   "words_removed": len(words_removed)},
        "align": align_used,
        "warnings": warnings,
    }


# ---------------------------------------------------------------- redline ---

_GRAY = 0.78
_RED = (0.84, 0.06, 0.06)               # the house red
_BLUE = (0.118, 0.314, 0.784)           # align.py's overlay blue — raster
#                                         and vector compares speak one
#                                         color language


def redline_pdf(base_pdf, rev_pdf, out_path, base_page: int = 1,
                rev_page: int = 1, *, align="auto", log=print) -> dict:
    """Write the one-page redline overlay and return the DiffReport.

    Unchanged linework gray (context, recedes); REMOVED dashed red (the
    demolition-plan convention); ADDED solid blue; change regions boxed
    with a dashed red rectangle + a bold Δn tag.  NOTE: region markers
    are deliberately RECTANGLES, not revision clouds — invariant #6
    reserves cloud shapes, and clouded compare output is pending the
    owner's explicit sign-off (see HANDOFF).  Deterministic bytes
    (minipdf: content-hash /ID, no metadata)."""
    rep = diff_pages(base_pdf, rev_pdf, base_page, rev_page,
                     align=align, log=log)
    w, h = rep["page_size"]
    from .minipdf import Canvas
    c = Canvas(str(out_path), pagesize=(w, h))

    def seg(x0, y0, x1, y1):            # engine space is y-down; pdf is up
        c.line(x0, h - y0, x1, h - y1)

    c.setLineWidth(0.4)
    c.setStrokeColorRGB(_GRAY, _GRAY, _GRAY)
    for x0, y0, x1, y1 in rep["base_segments"]:
        seg(x0, y0, x1, y1)
    c.setLineWidth(0.9)
    c.setStrokeColorRGB(*_RED)
    c.setDash([3, 2])
    for x0, y0, x1, y1 in rep["removed"]:
        seg(x0, y0, x1, y1)
    c.setDash()
    c.setStrokeColorRGB(*_BLUE)
    for x0, y0, x1, y1 in rep["added"]:
        seg(x0, y0, x1, y1)

    c.setLineWidth(1.2)
    c.setStrokeColorRGB(*_RED)
    c.setFillColorRGB(*_RED)
    c.setFont("Helvetica-Bold", 9)
    for i, reg in enumerate(rep["regions"], 1):
        x0, y0, x1, y1 = reg["bbox"]
        c.setDash([5, 3])
        c.rect(x0, h - y1, x1 - x0, y1 - y0, stroke=1, fill=0)
        c.setDash()
        # revision-delta tag: WinAnsi carries no Greek delta, so the
        # triangle is DRAWN (3 strokes) with the number beside it
        tx, ty = x0 + 6, h - y0 + 4
        c.line(tx - 4, ty, tx + 4, ty)
        c.line(tx - 4, ty, tx, ty + 7)
        c.line(tx + 4, ty, tx, ty + 7)
        c.drawString(tx + 7, ty + 0.5, str(i))

    t = rep["totals"]
    lines = [
        f"{len(rep['regions'])} change region(s) — "
        f"{t['added']} added / {t['removed']} removed segment piece(s), "
        f"{t['added_len_pt']:g} pt added / "
        f"{t['removed_len_pt']:g} pt removed",
    ]
    if t["words_added"] or t["words_removed"]:
        lines.append(f"text: {t['words_added']} word(s) added, "
                     f"{t['words_removed']} removed")
    if rep["align"]:
        al = rep["align"]
        lines.append(f"aligned dx {al['dx']:.1f} dy {al['dy']:.1f} pt, "
                     f"rot {al['rotation']:.2f}° "
                     f"(score {al['score']:.2f})")
    lines.extend(rep["warnings"])
    c.setFont("Helvetica-Bold", 8)
    c.setFillColorRGB(*_RED)
    c.drawString(14, 30 + 10 * len(lines), "SLIPSHEET COMPARE — "
                 "removed dashed red / added solid blue / unchanged gray")
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(0.25, 0.25, 0.25)
    for i, txt in enumerate(lines):
        c.drawString(14, 30 + 10 * (len(lines) - 1 - i), txt)
    c.showPage()
    c.save()
    return rep
