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
# ---- P5 lattice (split + merge + language prior) --------------------------
LM_ALPHA = 0.6           # channel weight; (1-α) on the char bigram prior
MERGE_GAP_FACTOR = 0.25  # merge fence: a bigger gap is a REAL space — never
#                          re-weld words the scanner didn't
MERGE_MIN_H = 0.5        # a merge flank must be at least this × the word's
#                          median glyph height: broken-LETTER fragments are
#                          tall; a trailing period / apostrophe is a real
#                          mark, not a fragment (measured: the merge move
#                          swallowed clean trailing periods)
SEG_BIAS = 0.0           # per-segment offset κ (log domain removed the
#                          positive-sum over-split bias; sweep ±0.05 against
#                          the eval before touching)
_LOG_EPS = 1e-6
# word re-tokenization (P5): toner dilation FATTENS glyphs and shrinks the
# inter-word gap below Wong's absolute rule, fusing words box-level with
# no weld at all — a real space then reads as an OUTLIER against the
# word's own inter-character gaps
RETOK_OUTLIER = 2.5      # gap >= this × the word's median inter-char gap
RETOK_FLOOR = 0.18       # ... and >= this × median glyph height (absolute)
RETOK_MIN_GAPS = 4       # outlier rule needs a stable in-word gap median
RETOK_MARKS = ".,'\""    # a space never PRECEDES these — the wide advance
#                          before a trailing period is not a word break
OVERWIDE_LAMBDA = 2.0    # channel penalty rate for a segment wider than the
#                          glyph band: the classifier's normalized cell can't
#                          see absolute width, and a two-glyph weld reads as a
#                          CONFIDENT single W/M (measured 0.84-0.92) — beyond
#                          SEG_W_HI that confidence is discounted, while a
#                          genuine wide glyph still wins because its shreds
#                          read as garbage, not just mediocre
SEG_SURE_CONF = 0.95     # ...but the discount models that MASQUERADE band
#                          (dilated welds 0.84-0.92, Hershey welds <= 0.80):
#                          a whole reading at/above this bar is a genuine
#                          wide glyph (M/W/0/Q measure >= 0.95) and is never
#                          discounted — the discount alone was shredding a
#                          degraded '0' (conf 1.00, 1.91x the median) into
#                          two mediocre '1's
MED_RELIABLE_N = 8       # the width-band penalty needs a TRUSTWORTHY median:
#                          on a short token line (a lone sheet number) med_w
#                          swings with a single wide char and a genuine 0/M
#                          reads as "overwide" — penalize only when the line
#                          gave at least this many glyphs


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


def _dropfall_cut(crop: np.ndarray, seed_col: int,
                  ascending: bool = False) -> int:
    """Refine a cut near ``seed_col`` with a drop-fall water path.

    Starting at the seeded column the path walks row by row choosing the
    least-ink move of {straight, diagonal-left, diagonal-right}, so the cut
    skirts strokes rather than slicing through them.  The classic family
    has descending and ascending variants: a top-seeded (descending) path
    hugs the top contour and can miss a weld near the BASELINE — the
    ascending path (bottom→top) recovers those.  Returns the median column
    of the path.  Pure numpy/stdlib, deterministic.
    """
    H, W = crop.shape
    ink = crop.astype(bool)
    col = int(np.clip(seed_col, 1, W - 2))
    rows = range(H - 1, -1, -1) if ascending else range(H)
    step = -1 if ascending else 1
    cols = []
    for r in rows:
        cols.append(col)
        nxt = min(max(r + step, 0), H - 1)
        best = col
        best_ink = ink[nxt, col]
        for dc in (-1, 1):
            c2 = col + dc
            if 1 <= c2 <= W - 2:
                v = ink[nxt, c2]
                if int(v) < int(best_ink):
                    best_ink, best = v, c2
        col = best
    return int(np.median(cols)) if cols else seed_col


def _stroke_w(crop: np.ndarray) -> float:
    """Median horizontal ink-run length — the pen-stroke thickness."""
    runs = []
    for row in crop.astype(bool):
        n = 0
        for v in row:
            if v:
                n += 1
            elif n:
                runs.append(n)
                n = 0
        if n:
            runs.append(n)
    return float(np.median(runs)) if runs else 1.0


def candidate_cuts(crop: np.ndarray, med_w: float) -> list:
    """Over-segmentation cut columns: pitch guesses snapped to valleys.

    ``n = round(width/pitch)`` ideal boundaries, each searched within ±0.3×
    median width for the lowest projection column and refined by BOTH
    drop-fall variants (descending + ascending — a top-seeded path misses
    welds near the baseline); interior valleys are admitted by the NECK
    test — a genuine weld neck carries about one pen stroke of ink
    (``prof[c] <= 1.5 × stroke_w``), which out-generates the old
    mean-relative rule on short two-glyph crops where the glyphs dominate
    the mean.  Total cuts are capped to bound the recombination lattice.
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
        cuts.add(int(np.clip(_dropfall_cut(crop, local, ascending=True),
                             1, W - 1)))
    # neck-test valleys: local minima thin enough to be a weld bridge
    neck = 1.5 * _stroke_w(crop)
    for c in range(2, W - 2):
        if prof[c] <= prof[c - 1] and prof[c] < prof[c + 1] \
                and (prof[c] < 0.6 * prof.mean() or prof[c] <= neck):
            cuts.add(c)
    cap = 2 * int(round(W / max(1.0, med_w))) + 2
    if len(cuts) > cap:                     # keep the thinnest necks
        cuts = set(sorted(cuts, key=lambda c: (prof[c], c))[:cap])
    return sorted(cuts)


def _lattice_spans(crop, B, med_w, clf, gaps=(), always=(), med_n=0,
                   centered=None, penalize=None):
    """The P5 recombination lattice: Viterbi over (boundary, last char).

    ``B`` are sorted boundary columns; a segment ``[B[i], B[j])`` is
    admissible when its width sits in the glyph band — or it is listed in
    ``always`` (each primitive box's own full span: the WHOLE reading
    must compete inside the lattice, never be gated out, so a genuine
    wide glyph against a small-sample median survives) — its ink is
    non-empty, and it never CONTAINS a non-mergeable gap from ``gaps``
    (``(gs, ge, mergeable)`` runs of blank columns between primitives —
    the broken-glyph merge move crosses only mergeable ones).  Every
    admissible segment is normalized once and classified in ONE
    ``classify_batch`` call (per-segment classify inside the loop is the
    O(B²)-model-calls trap); its channel term is ``α·ln p_clf`` WEIGHTED
    BY WIDTH (w/med_w — per-unit-evidence scoring: without it a
    confident single misread of a two-glyph weld beats the correct
    split simply by having fewer terms, the mirror image of the old
    positive-sum over-split bias), plus ``(1−α)·ln P(c|c')`` from the
    char bigram prior per transition.  Anchored with ``^``/``$``; ties
    break by iteration order (lower i, then ranked char order) —
    determinism.  Returns ``[(x0, x1), ...]`` or None when no path.
    """
    import math

    from . import lexicon as _lex
    from .fonts import CHARSET
    from .normalize import norm_glyph
    m = len(B)
    always = set(always)
    cand, cells, aspects = [], [], []
    free = set()
    for i in range(m - 1):
        for j in range(i + 1, m):
            sub = crop[:, B[i]:B[j]]
            if not sub.any():
                if j == i + 1:
                    # FREE CONNECTOR: a no-ink stretch (ordinary
                    # inter-glyph spacing) advances the path without
                    # emitting a glyph — without these the word lattice
                    # has NO complete path across normal letter gaps and
                    # every word silently falls back to the per-box path
                    free.add((i, j))
                continue
            w = B[j] - B[i]
            if not (SEG_W_LO * med_w <= w <= SEG_W_HI * med_w) \
                    and (B[i], B[j]) not in always:
                continue
            ok = True
            for gs, ge, mergeable in gaps:
                if B[i] <= gs and B[j] >= ge + 1 and not mergeable:
                    ok = False
                    break
            if not ok:
                continue
            ng = norm_glyph(sub)
            cand.append((i, j))
            cells.append(ng.cell)
            aspects.append(ng.aspect)
    if not cand:
        return None
    ranked = clf.classify_batch(np.stack(cells), aspects,
                                [0.5] * len(cells))
    if centered is None:            # word lattice: the reliability gate
        centered = med_n >= MED_RELIABLE_N
    lm = _lex.bigram_lp(centered=centered)
    cidx = {c: k for k, c in enumerate(CHARSET)}
    anchor = len(CHARSET)
    segs: dict = {}
    for (i, j), rk in zip(cand, ranked):
        segs.setdefault((i, j), []).extend(
            (ch, float(p)) for ch, p in rk[:3])
    # Viterbi: best[j][last_char] = (score, (i, prev_char, char));
    # char None = a free gap connector (no glyph, state carried across)
    best = [dict() for _ in range(m)]
    best[0][anchor] = (0.0, None)
    for (i, j) in sorted(set(segs) | free):
        if not best[i]:
            continue
        if (i, j) in free:
            for cp in sorted(best[i]):
                sc = best[i][cp][0]
                if cp not in best[j] or sc > best[j][cp][0]:
                    best[j][cp] = (sc, (i, cp, None))
            continue
        weight = (B[j] - B[i]) / max(med_w, 1.0)
        pen_on = (med_n >= MED_RELIABLE_N) if penalize is None else penalize
        overwide = max(0.0, weight - SEG_W_HI) if pen_on else 0.0
        for ch, p in segs[(i, j)]:
            ci = cidx.get(ch)
            if ci is None:
                continue
            p_eff = (p if p >= SEG_SURE_CONF
                     else p * math.exp(-OVERWIDE_LAMBDA * overwide))
            chan = (LM_ALPHA * math.log(max(p_eff, _LOG_EPS))
                    + SEG_BIAS) * weight
            for cp in sorted(best[i]):
                sc = (best[i][cp][0] + chan
                      + (1.0 - LM_ALPHA) * float(lm[cp, ci]))
                if ci not in best[j] or sc > best[j][ci][0]:
                    best[j][ci] = (sc, (i, cp, ch))
    if not best[m - 1]:
        return None
    fin = {ci: (sc + (1.0 - LM_ALPHA) * float(lm[ci, anchor]), bk)
           for ci, (sc, bk) in best[m - 1].items()}
    ci = max(sorted(fin), key=lambda c: fin[c][0])
    spans = []
    j = m - 1
    while j > 0:
        i, cp, ch = best[j][ci][1]
        if ch is not None:                  # skip the free gap connectors
            spans.append((B[i], B[j]))
        j, ci = i, cp
    spans.reverse()
    return spans


def dp_recombine(crop, cuts, med_w, clf, med_n=0):
    """Partition one wide crop at the best cut subset (P5 lattice: batched
    classification + char bigram prior, width-weighted log domain).  The
    whole crop always competes as one segment.  Returns the ``(x0, x1)``
    spans of the winning partition (⩾ 1 span); no admissible path falls
    back to the whole box — today's honest behavior."""
    W = crop.shape[1]
    B = sorted(set([0] + [c for c in cuts if 0 < c < W] + [W]))
    # a lone box carries no marks/merge context, so the length-neutral
    # (centered) prior is always safe here — without it an isolated weld
    # pays a flat LM toll per extra glyph and never splits
    spans = _lattice_spans(crop, B, med_w, clf, always={(0, W)},
                           med_n=med_n, centered=True, penalize=True)
    return spans if spans else [(0, W)]


def split_glyph_boxes(ink, box, med_w: float, clf, med_n: int = 0):
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
    # the confident-whole early-exit protects wide SINGLE glyphs (M, W, 0)
    # — but past SEG_W_HI no single glyph is that wide, and the classifier
    # reads two-glyph welds as confident single chars (measured 0.84-0.92
    # on the touching tier); beyond the band the exit cannot apply
    if box.w <= SEG_W_HI * med_w \
            and clf.classify(ng.cell, ng.aspect, 0.5)[0][1] >= SPLIT_WHOLE_CONF:
        return whole
    cuts = candidate_cuts(crop, med_w)
    spans = dp_recombine(crop, cuts, med_w, clf, med_n=med_n)
    if len(spans) <= 1:
        return whole
    out = []
    for (a, b) in spans:
        sub = crop[:, a:b]
        rows = np.where(sub.any(axis=1))[0]
        cols = np.where(sub.any(axis=0))[0]
        if rows.size == 0:
            continue
        # trim to the glyph's own ink COLUMNS too: cut boundaries sit at
        # the weld neck, so untrimmed spans touch and a weld across a real
        # SPACE could never re-open into two words downstream
        out.append((y0 + int(rows.min()), x0 + a + int(cols.min()),
                    y0 + int(rows.max()), x0 + a + int(cols.max())))
    return out or whole


def word_spans(ink, boxes, med_w: float, clf, med_n: int = 0):
    """Absolute ``(y0, x0, y1, x1)`` glyph spans for ONE word's boxes —
    the P5 split+merge lattice.

    Fast path first: every box is classified whole (one batch); when no
    box is BOTH wide and unconfident (split candidate) and no adjacent
    pair sits within the merge fence with an unconfident flank (broken-
    glyph candidate), the word takes today's exact per-box path — clean
    pages never pay for the lattice, and two CONFIDENT glyphs are never
    merge candidates (an L+I can't re-weld into a U).  Otherwise one
    lattice runs over the whole word: primitives' edges + the candidate
    cuts of split candidates form the boundary list, segments may cross
    only mergeable gaps, and the bigram-prior Viterbi picks the
    partition.  No admissible path → per-box fallback (today's
    behavior).
    """
    from .normalize import norm_glyph
    boxes = sorted(boxes, key=lambda b: b.x0)
    if not boxes:
        return []
    cells, aspects = [], []
    for b in boxes:
        sub = ink[b.y0:b.y1 + 1, b.x0:b.x1 + 1]
        ng = norm_glyph(sub if sub.any() else np.ones((3, 3), bool))
        cells.append(ng.cell)
        aspects.append(ng.aspect)
    ranked = clf.classify_batch(np.stack(cells), aspects,
                                [0.5] * len(cells))
    conf = [float(r[0][1]) for r in ranked]
    wide = [b.w > TOUCH_WIDE_FACTOR * med_w and med_w > 0 for b in boxes]
    # a wide box splits when it reads poorly as one glyph OR is wider than
    # any single glyph can be (SEG_W_HI) — welds read as CONFIDENT single
    # chars, so past the band confidence cannot exempt them
    split_cand = [wide[k] and (conf[k] < SPLIT_WHOLE_CONF
                               or boxes[k].w > SEG_W_HI * med_w)
                  for k in range(len(boxes))]
    med_h_word = _median([b.h for b in boxes]) or 1.0
    merge_cand = []
    for k in range(len(boxes) - 1):
        gap = boxes[k + 1].x0 - boxes[k].x1 - 1
        merge_cand.append(
            0 <= gap <= MERGE_GAP_FACTOR * med_w
            and min(conf[k], conf[k + 1]) < SPLIT_WHOLE_CONF
            and min(boxes[k].h, boxes[k + 1].h) >= MERGE_MIN_H * med_h_word)
    if not any(split_cand) and not any(merge_cand):
        out = []                        # today's exact per-box path
        for b in boxes:
            out.extend(split_glyph_boxes(ink, b, med_w, clf, med_n=med_n))
        return out

    x0w = boxes[0].x0
    x1w = max(b.x1 for b in boxes)
    y0w = min(b.y0 for b in boxes)
    y1w = max(b.y1 for b in boxes)
    # MASKED crop: only the word's own component boxes contribute ink.
    # A degraded page's stray speckle (already rejected by filter_glyphs
    # as sub-glyph) otherwise blocks the free connectors — voiding the
    # whole lattice into the per-box fallback — and rides into a span's
    # full-height ink trim (a speck above the dash read '-' as ').
    crop = np.zeros((y1w - y0w + 1, x1w - x0w + 1), dtype=bool)
    for b in boxes:
        crop[b.y0 - y0w:b.y1 + 1 - y0w, b.x0 - x0w:b.x1 + 1 - x0w] |= \
            ink[b.y0:b.y1 + 1, b.x0:b.x1 + 1]
    B = set()
    gaps = []
    always = set()                      # each box WHOLE always competes
    for k, b in enumerate(boxes):
        B.add(b.x0 - x0w)
        B.add(b.x1 + 1 - x0w)
        always.add((b.x0 - x0w, b.x1 + 1 - x0w))
        if split_cand[k]:
            bc = ink[b.y0:b.y1 + 1, b.x0:b.x1 + 1]
            for c in candidate_cuts(bc, med_w):
                B.add(b.x0 - x0w + c)
        if k < len(boxes) - 1:
            gs = b.x1 + 1 - x0w
            ge = boxes[k + 1].x0 - 1 - x0w
            if ge >= gs:
                gaps.append((gs, ge, merge_cand[k]))
    B = sorted(v for v in B if 0 <= v <= crop.shape[1])
    spans = _lattice_spans(crop, B, med_w, clf, gaps=tuple(gaps),
                           always=always, med_n=med_n)
    if not spans:
        out = []                        # no admissible path: today's path
        for b in boxes:
            out.extend(split_glyph_boxes(ink, b, med_w, clf, med_n=med_n))
        return out
    out = []
    for (a, b2) in spans:
        sub = crop[:, a:b2]
        rows = np.where(sub.any(axis=1))[0]
        cols = np.where(sub.any(axis=0))[0]
        if rows.size == 0:
            continue
        # ink-column trim (see split_glyph_boxes): real spaces reappear
        # between cut spans so the word re-tokenizer can re-open them
        out.append((y0w + int(rows.min()), x0w + a + int(cols.min()),
                    y0w + int(rows.max()), x0w + a + int(cols.max())))
    if not out:
        out = []
        for b in boxes:
            out.extend(split_glyph_boxes(ink, b, med_w, clf, med_n=med_n))
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
