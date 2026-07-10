"""The Story Pole — dimension-anchored autoscale, witnessed (SETSCAN Phase 1).

A story pole is the carpenter's rod marked with known lengths, used to
transfer and VERIFY measurements.  This module derives a sheet's true
scale (points per real foot) from its own dimension strings paired with
their dimension lines — and refuses to answer unless independent
witnesses agree.  Certainty is a verdict, not a boolean: every verdict
carries its witnesses, its named outliers, its door-opening checks and
its title-block note check, and a REFUSED verdict says exactly why.

The certainty contract (all applied per sheet, never inherited):

1. At least ``min_witnesses`` dimension witnesses within ±0.5 % of the
   median hypothesis.  Outliers are NAMED (a mistyped dimension on the
   drawing is found, not averaged in).
2. An independent corroborator beyond the dimensions' self-agreement:
   door openings landing on standard leaf sizes (2'-0"…4'-0" in 2"
   steps), or an agreeing title-block scale note.  Dimensions alone can
   be consistently wrong (a half-size print is self-consistent), so
   self-agreement without a second family of evidence REFUSES.
3. A title-block scale note that DISAGREES refuses loudly with the exact
   ratio — the classic printed-half-size set is caught, not mismeasured.

Vector sheets only (the scope fence): a scanned set has no text layer or
line segments to harvest, and refuses with "no dimension witnesses".
"""
from __future__ import annotations

import math
import re
import statistics

import fitz

from .draft import _num, parse_ftin

# hypothesis harvesting
MIN_SEG_PT = 4.0          # ignore tick-length fragments
MID_BAND = (0.18, 0.82)   # text must project into the line's middle band
PPF_MIN, PPF_MAX = 1.5, 150.0   # 1/48" = 1'-0" … 2" = 1'-0" plus print slop
MIN_VALUE_FT = 0.9        # sub-foot strings ("6\"" labels) are not anchors

# the certainty contract
MIN_WITNESSES = 5
WITNESS_TOL = 0.005       # ±0.5 % of the median hypothesis
NOTE_TOL = 0.01
MIN_DOORS = 2
DOOR_TOL_IN = 0.75        # leaf must land this close to a standard size
STD_LEAF_IN = tuple(range(24, 49, 2))   # 2'-0" … 4'-0" in 2" steps

# the architectural scale ladder, for the human-readable verdict label
_LADDER = (
    ("3\" = 1'-0\"", 18.0), ("1 1/2\" = 1'-0\"", 9.0), ("1\" = 1'-0\"", 6.0),
    ("3/4\" = 1'-0\"", 4.5), ("1/2\" = 1'-0\"", 3.0), ("3/8\" = 1'-0\"", 2.25),
    ("1/4\" = 1'-0\"", 1.5), ("3/16\" = 1'-0\"", 1.125), ("1/8\" = 1'-0\"", 0.75),
    ("3/32\" = 1'-0\"", 0.5625), ("1/16\" = 1'-0\"", 0.375),
)


def _ftin_tokens(page) -> list:
    """Dimension-string candidates -> [(text, value_ft, rect)].

    Word-joins up to three adjacent words on a line (``12'-4`` + ``1/2"``)
    keeping the LONGEST successful parse per start; a candidate must carry
    a foot mark — bare numbers are far too common on a drawing to anchor a
    scale on."""
    lines: dict = {}
    for w in page.get_text("words"):
        lines.setdefault((w[5], w[6]), []).append(w)
    out = []
    for ws in lines.values():
        ws.sort(key=lambda w: w[7])
        for i in range(len(ws)):
            if i > 0 and ws[i - 1][4].strip() == "=":
                continue            # the "= 1'-0\"" tail of a scale note
            best = None
            joined = ""
            rect = fitz.Rect(ws[i][:4])
            for k in range(i, min(i + 3, len(ws))):
                joined = (joined + " " + ws[k][4]).strip()
                rect.include_rect(fitz.Rect(ws[k][:4]))
                if "'" not in joined and "’" not in joined:
                    continue
                v = parse_ftin(joined)
                if v is not None and v >= MIN_VALUE_FT:
                    best = (joined, v, fitz.Rect(rect))
            if best:
                out.append(best)
    # drop candidates fully contained in a longer neighbour (the "1/2\""
    # tail of "12'-4 1/2\"" must not also witness on its own)
    keep = []
    for t in out:
        if not any(o is not t and o[2].contains(t[2]) and len(o[0]) > len(t[0])
                   for o in out):
            keep.append(t)
    return keep


def _segments(page) -> list:
    """Stroked line segments -> [(p0, p1, length_pt)]."""
    segs = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                p0, p1 = it[1], it[2]
                ln = math.hypot(p1.x - p0.x, p1.y - p0.y)
                if ln >= MIN_SEG_PT:
                    segs.append(((p0.x, p0.y), (p1.x, p1.y), ln))
    return segs


def _dim_hypotheses(page, tokens=None, segs=None) -> list:
    """Pair each dimension string with its dimension line.

    The dimension text sits centered just off the middle of its line, so
    the nearest segment (by perpendicular distance) whose middle band
    contains the text's projection is the line the string measures."""
    tokens = _ftin_tokens(page) if tokens is None else tokens
    segs = _segments(page) if segs is None else segs
    hyps = []
    for text, value, rect in tokens:
        cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
        reach = 3.0 * max(4.0, min(rect.width, rect.height))
        best = None
        for p0, p1, ln in segs:
            ux, uy = (p1[0] - p0[0]) / ln, (p1[1] - p0[1]) / ln
            proj = (cx - p0[0]) * ux + (cy - p0[1]) * uy
            if not (MID_BAND[0] * ln <= proj <= MID_BAND[1] * ln):
                continue
            perp = abs((cx - p0[0]) * -uy + (cy - p0[1]) * ux)
            if perp <= reach and (best is None or perp < best[0]):
                best = (perp, ln)
        if best is None:
            continue
        ppf = best[1] / value
        if PPF_MIN <= ppf <= PPF_MAX:
            hyps.append({"text": text, "value_ft": value,
                         "line_pt": best[1], "ppf": ppf})
    return hyps


def _door_candidates(page, segs=None) -> list:
    """Door swing arcs -> [{"center", "r_pt"}].

    A swing is a ~90° arc whose radius equals the leaf length, drawn with
    a leaf line anchored at the hinge (the arc's center).  Curve points of
    each drawing are circle-fitted (Kåsa least squares); a candidate needs
    a low fit residual, a quarter-ish angular span, and that anchored leaf
    line — which is what keeps ordinary decorative arcs out."""
    segs = _segments(page) if segs is None else segs
    out = []
    for d in page.get_drawings():
        pts = []
        for it in d["items"]:
            if it[0] == "c":
                b = [it[1], it[2], it[3], it[4]]
                for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                    x = ((1 - t) ** 3 * b[0].x + 3 * (1 - t) ** 2 * t * b[1].x
                         + 3 * (1 - t) * t ** 2 * b[2].x + t ** 3 * b[3].x)
                    y = ((1 - t) ** 3 * b[0].y + 3 * (1 - t) ** 2 * t * b[1].y
                         + 3 * (1 - t) * t ** 2 * b[2].y + t ** 3 * b[3].y)
                    pts.append((x, y))
        if len(pts) < 5:
            continue
        # Kåsa fit: minimize sum((x²+y²) + D·x + E·y + F)²
        sxx = syy = sxy = sx = sy = sz = szx = szy = 0.0
        n = len(pts)
        for x, y in pts:
            z = x * x + y * y
            sxx += x * x
            syy += y * y
            sxy += x * y
            sx += x
            sy += y
            sz += z
            szx += z * x
            szy += z * y
        det = (sxx * (syy * n - sy * sy) - sxy * (sxy * n - sy * sx)
               + sx * (sxy * sy - syy * sx))
        if abs(det) < 1e-9:
            continue
        dd = (-(szx * (syy * n - sy * sy) - sxy * (szy * n - sy * sz)
                + sx * (szy * sy - syy * sz)) / det)
        ee = (-(sxx * (szy * n - sz * sy) - szx * (sxy * n - sy * sx)
                + sx * (sxy * sz - szy * sx)) / det)
        ff = (-(sxx * (syy * sz - szy * sy) - sxy * (sxy * sz - szy * sx)
                + szx * (sxy * sy - syy * sx)) / det)
        cx, cy = -dd / 2, -ee / 2
        r2 = cx * cx + cy * cy - ff
        if r2 <= 0:
            continue
        r = math.sqrt(r2)
        if not (6.0 <= r <= 400.0):
            continue
        if any(abs(math.hypot(x - cx, y - cy) - r) > 0.02 * r + 0.5
               for x, y in pts):
            continue
        angs = [math.atan2(y - cy, x - cx) for x, y in pts]
        lo, hi = min(angs), max(angs)
        span = math.degrees(min(hi - lo, 2 * math.pi - (hi - lo)))
        if not (55.0 <= span <= 125.0):
            continue
        anchor = max(0.05 * r, 1.5)
        if any((math.hypot(p0[0] - cx, p0[1] - cy) <= anchor
                or math.hypot(p1[0] - cx, p1[1] - cy) <= anchor)
               and abs(ln - r) <= 0.06 * r
               for p0, p1, ln in segs):
            out.append({"center": (cx, cy), "r_pt": r})
    return out


_NOTE_ARCH = re.compile(
    r"(\d+(?:\s+\d+/\d+)?(?:/\d+)?)\s*[\"”]\s*=\s*1\s*['’]\s*-?\s*0?[\"”]?")
_NOTE_ENG = re.compile(r"1\s*[\"”]\s*=\s*(\d+(?:\.\d+)?)\s*['’]")


def _scale_note(page) -> dict | None:
    """Title-block scale note -> {"label", "ppf"} (None when absent).

    Distinct conflicting notes (enlarged-detail sheets) come back with
    ``ppf None`` and both labels — ambiguity is surfaced, never picked
    from."""
    found = {}
    text = page.get_text()
    for m in _NOTE_ARCH.finditer(text):
        v = _num(m.group(1))
        if v:
            found[round(v * 72.0, 6)] = m.group(0).strip()
    for m in _NOTE_ENG.finditer(text):
        v = _num(m.group(1))
        if v:
            found[round(72.0 / float(v), 6)] = m.group(0).strip()
    if not found:
        return None
    if len(found) > 1:
        return {"label": " / ".join(sorted(found.values())), "ppf": None}
    ppf, label = next(iter(found.items()))
    return {"label": label, "ppf": ppf}


def scale_label(ppf: float) -> str:
    """Nearest architectural ladder label (annotated when off-ladder)."""
    best = min(_LADDER, key=lambda e: abs(e[1] * 12.0 - ppf))
    if abs(best[1] * 12.0 - ppf) <= 0.01 * ppf:
        return best[0]
    return f"{ppf / 72.0 * 12.0:.4g}\" = 1'-0\" (non-standard)"


def sheet_verdict(page, min_witnesses: int = MIN_WITNESSES,
                  min_doors: int = MIN_DOORS) -> dict:
    """Run the certainty contract over one page.

    Returns ``{"status": "PASS"|"REFUSED", "pt_per_ft", "label",
    "reasons", "witnesses", "outliers", "door_checks", "note"}``."""
    segs = _segments(page)
    hyps = _dim_hypotheses(page, segs=segs)
    note = _scale_note(page)
    verdict = {"status": "REFUSED", "pt_per_ft": None, "label": None,
               "reasons": [], "witnesses": [], "outliers": [],
               "door_checks": [], "note": note}

    def refuse(reason):
        verdict["reasons"].append(reason)
        return verdict

    if not hyps:
        return refuse("no dimension witnesses found (scanned or "
                      "dimension-free sheet)")
    med = statistics.median(h["ppf"] for h in hyps)
    for h in hyps:
        if abs(h["ppf"] / med - 1.0) <= WITNESS_TOL:
            verdict["witnesses"].append(h)
        else:
            o = dict(h)
            o["implied_ratio"] = round(h["ppf"] / med, 4)
            verdict["outliers"].append(o)
    if len(verdict["witnesses"]) < min_witnesses:
        return refuse(f"only {len(verdict['witnesses'])} agreeing dimension "
                      f"witness(es) — need {min_witnesses}")
    ppf = statistics.median(h["ppf"] for h in verdict["witnesses"])

    doors_ok = 0
    for c in _door_candidates(page, segs=segs):
        leaf_in = c["r_pt"] / ppf * 12.0
        std = min(STD_LEAF_IN, key=lambda s: abs(s - leaf_in))
        ok = abs(leaf_in - std) <= DOOR_TOL_IN
        doors_ok += ok
        verdict["door_checks"].append(
            {"r_pt": round(c["r_pt"], 2), "leaf_in": round(leaf_in, 2),
             "nearest_std_in": std, "ok": ok})
    n_doors = len(verdict["door_checks"])

    note_agrees = None
    if note is not None and note["ppf"] is not None:
        ratio = ppf / note["ppf"]
        note_agrees = abs(ratio - 1.0) <= NOTE_TOL
        note["measured_ratio"] = round(ratio, 4)
        if not note_agrees:
            return refuse(
                f"measured scale is {ratio:.3f}x the title-block note "
                f"({note['label']}) — printed at reduced/enlarged size? "
                "Calibrating to this sheet would mismeasure everything")

    door_gate = (n_doors > 0 and doors_ok >= min(min_doors, n_doors)
                 and doors_ok * 2 >= n_doors)
    if not door_gate and note_agrees is not True:
        if n_doors:
            return refuse(
                f"door openings do not corroborate ({doors_ok} of "
                f"{n_doors} land on standard leaf sizes) and no agreeing "
                "scale note")
        return refuse(
            "dimensions self-agree but nothing independent corroborates "
            "(no door swings found, no title-block scale note) — a "
            "reduced print is self-consistent too, so this refuses")

    verdict["status"] = "PASS"
    verdict["pt_per_ft"] = ppf
    verdict["label"] = scale_label(ppf)
    verdict["reasons"].append(
        f"{len(verdict['witnesses'])} dimension witnesses agree at "
        f"{ppf:.3f} pt/ft"
        + (f"; {doors_ok}/{n_doors} door openings corroborate"
           if n_doors else "")
        + ("; title-block note agrees" if note_agrees else ""))
    return verdict


def set_verdicts(path: str, min_witnesses: int = MIN_WITNESSES,
                 min_doors: int = MIN_DOORS) -> list:
    """Per-sheet verdicts for a whole set (1-based page numbers).

    Every sheet verifies independently — enlarged plans at 1/4" sit next
    to 1/8" floor plans on real sets, so scale is never inherited."""
    doc = fitz.open(path)
    try:
        out = []
        for pno in range(doc.page_count):
            v = sheet_verdict(doc[pno], min_witnesses=min_witnesses,
                              min_doors=min_doors)
            v["page"] = pno + 1
            out.append(v)
        return out
    finally:
        doc.close()
