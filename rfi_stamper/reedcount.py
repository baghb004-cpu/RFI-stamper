"""The Reed Count — fixture-symbol recognition + auto-count (SETSCAN Phase 2).

The reed count is the loom's dents-per-inch: THE density count of the
trade.  This module counts plumbing fixtures on vector sheets by
matching linework clusters against a symbol library seeded from
Planloom's own Loft stencils (drawn to the common drafting conventions,
described convention-only).

Method: strip the linework that can't be a fixture (long wall/grid
segments, door swings via the Story Pole's arc detector), cluster what
remains by proximity, normalize each cluster (translation / rotation /
uniform-scale / reflection invariant via principal-axis canonicalization
+ an 8-pose search), and score a grid-occupancy IoU against every
library signature.

Honesty rules (the Reed Count is a PROPOSAL engine, never a deliverable):

* Size sanity is a hard gate and the reason this phase depends on the
  Story Pole: with a verified scale, a water-closet candidate must
  footprint ~19"x28" real — a perfect shape at an impossible size is
  REJECTED with the reason, not counted.
* Unmatched fixture-sized clusters land in the "unknown" tray with their
  best near-miss named; the user can label one and the labeled shape
  joins the library (human-gated, the review-deck precedent).
* Every exclusion is counted and surfaced (long linework, door swings,
  size rejections) — silence never means "nothing was there".
"""
from __future__ import annotations

import math

from .draft import STENCILS

# clustering / filtering
GAP_IN = 3.0              # primitives closer than this (real) join a cluster
MAX_PRIM_FT = 6.5         # any single primitive longer is linework, not fixture
MIN_PRIMS = 3             # a fixture symbol is never one lonely stroke
SIZE_MIN_FT, SIZE_MAX_FT = 0.20, 6.6    # fixture-scale bbox band

# matching
GRID_N = 24
GRID_SPAN = 1.15          # normalized half-extent covered by the grid
N_ROT = 24                # brute rotation search, 15° steps (x2 for flip) —
                          # principal-axis canonicalization is UNSTABLE for
                          # symmetric shapes (a square's axis is arbitrary)
MATCH_SCORE = 0.72        # at/above: a hit (subject to the size gate)
NEAR_SCORE = 0.45         # tray entries name their nearest miss above this
AMBIG_MARGIN = 0.06       # two size-sane candidates this close = AMBIGUOUS
SIZE_TOL = 0.35           # footprint sanity band (±35 %)
SAMPLES = 220             # target sample points per shape


# --------------------------------------------------------------------------- #
#  primitive extraction                                                       #
# --------------------------------------------------------------------------- #

def _bez(b, t):
    x = ((1 - t) ** 3 * b[0][0] + 3 * (1 - t) ** 2 * t * b[1][0]
         + 3 * (1 - t) * t ** 2 * b[2][0] + t ** 3 * b[3][0])
    y = ((1 - t) ** 3 * b[0][1] + 3 * (1 - t) ** 2 * t * b[1][1]
         + 3 * (1 - t) * t ** 2 * b[2][1] + t ** 3 * b[3][1])
    return (x, y)


def _prims_from_page(page) -> list:
    """Vector primitives -> [{"pts": polyline, "len": pt}] (page points)."""
    prims = []

    def add(pts):
        ln = sum(math.hypot(pts[i + 1][0] - pts[i][0],
                            pts[i + 1][1] - pts[i][1])
                 for i in range(len(pts) - 1))
        if ln > 0:
            prims.append({"pts": pts, "len": ln})

    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                add([(it[1].x, it[1].y), (it[2].x, it[2].y)])
            elif it[0] == "c":
                b = [(p.x, p.y) for p in it[1:5]]
                add([_bez(b, k / 8.0) for k in range(9)])
            elif it[0] == "re":
                r = it[1]
                add([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1),
                     (r.x0, r.y1), (r.x0, r.y0)])
            elif it[0] == "qu":
                q = it[1]
                add([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y),
                     (q.ll.x, q.ll.y), (q.ul.x, q.ul.y)])
    return prims


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _clusters(prims, ppf: float, door_centers=()) -> tuple:
    """Proximity clusters of fixture-scale primitives.

    Returns (clusters, excluded) where excluded counts the linework and
    door-swing primitives that were removed first."""
    gap = GAP_IN / 12.0 * ppf
    keep, excluded = [], {"long_linework": 0, "door_swing": 0}
    for pr in prims:
        if pr["len"] > MAX_PRIM_FT * ppf:
            excluded["long_linework"] += 1
            continue
        if any(all(math.hypot(x - cx, y - cy) <= r * 1.12 + 1.0
                   for x, y in pr["pts"])
               for cx, cy, r in door_centers):
            excluded["door_swing"] += 1
            continue
        keep.append(pr)
    boxes = [_bbox(pr["pts"]) for pr in keep]
    parent = list(range(len(keep)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(keep)):
        x0, y0, x1, y1 = boxes[i]
        for j in range(i + 1, len(keep)):
            a0, b0, a1, b1 = boxes[j]
            if (x0 - gap <= a1 and a0 <= x1 + gap
                    and y0 - gap <= b1 and b0 <= y1 + gap):
                pi, pj = find(i), find(j)
                if pi != pj:
                    parent[pi] = pj
    groups: dict = {}
    for i in range(len(keep)):
        groups.setdefault(find(i), []).append(keep[i])
    return list(groups.values()), excluded


# --------------------------------------------------------------------------- #
#  normalization + grid signature                                             #
# --------------------------------------------------------------------------- #

def _resample(prims, target=SAMPLES) -> list:
    """Evenly-spaced points along all primitives (density-independent)."""
    total = sum(pr["len"] for pr in prims)
    if total <= 0:
        return []
    step = total / max(1, target)
    pts = []
    for pr in prims:
        poly = pr["pts"]
        carry = 0.0
        pts.append(poly[0])
        for i in range(len(poly) - 1):
            (x0, y0), (x1, y1) = poly[i], poly[i + 1]
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg <= 0:
                continue
            d = carry
            while d + step <= seg:
                d += step
                t = d / seg
                pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
            carry = (seg - d) % step if seg > d else carry - seg
        pts.append(poly[-1])
    return pts


def _grid(pts, rot: float, flip: bool) -> frozenset:
    """Occupied-cell set after rotate/flip + uniform-scale normalize."""
    c, s = math.cos(rot), math.sin(rot)
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    tp = []
    for x, y in pts:
        dx, dy = x - mx, y - my
        if flip:
            dx = -dx
        tp.append((dx * c - dy * s, dx * s + dy * c))
    m = max(max(abs(x), abs(y)) for x, y in tp) or 1.0
    cells = set()
    for x, y in tp:
        gx = int((x / m / GRID_SPAN + 1.0) * 0.5 * GRID_N)
        gy = int((y / m / GRID_SPAN + 1.0) * 0.5 * GRID_N)
        if 0 <= gx < GRID_N and 0 <= gy < GRID_N:
            cells.add(gx * GRID_N + gy)
    return frozenset(cells)


def _dilate(cells: frozenset) -> frozenset:
    """Cells + their 8-neighborhood: absorbs the one-cell sampling drift
    that makes raw grid IoU brittle."""
    out = set()
    for c in cells:
        gx, gy = divmod(c, GRID_N)
        for dx in (-1, 0, 1):
            nx = gx + dx
            if not (0 <= nx < GRID_N):
                continue
            for dy in (-1, 0, 1):
                ny = gy + dy
                if 0 <= ny < GRID_N:
                    out.add(nx * GRID_N + ny)
    return frozenset(out)


def _score(a: frozenset, da: frozenset, b: frozenset, db: frozenset) -> float:
    """Harmonic mean of the two dilated coverages (a soft F1)."""
    if not a or not b:
        return 0.0
    ca = len(a & db) / len(a)
    cb = len(b & da) / len(b)
    return 2.0 * ca * cb / (ca + cb) if (ca + cb) else 0.0


def _poses(pts) -> list:
    """Pose grids of a shape: N_ROT rotations x flip -> [(grid, dilated)]."""
    out = []
    for k in range(N_ROT):
        for f in (False, True):
            g = _grid(pts, k * 2.0 * math.pi / N_ROT, f)
            out.append((g, _dilate(g)))
    return out


# --------------------------------------------------------------------------- #
#  the symbol library                                                         #
# --------------------------------------------------------------------------- #

def _stencil_pts(spec) -> list:
    """Sample a stencil's local-inch ops into points (text ops carry no
    linework and are skipped — the PDF drawing layer has no text either)."""
    prims = []
    for op in spec["ops"]:
        if op[0] == "l":
            pts = [(op[1], op[2]), (op[3], op[4])]
        elif op[0] == "c":
            cx, cy, r = op[1], op[2], op[3]
            pts = [(cx + r * math.cos(t * math.pi / 12.0),
                    cy + r * math.sin(t * math.pi / 12.0))
                   for t in range(25)]
        elif op[0] == "a":
            cx, cy, r, a0, a1 = op[1], op[2], op[3], op[4], op[5]
            if a1 < a0:
                a1 += 360.0
            pts = [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / 12)),
                    cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / 12)))
                   for k in range(13)]
        elif op[0] == "e":
            cx, cy, rx, ry = op[1], op[2], op[3], op[4]
            pts = [(cx + rx * math.cos(t * math.pi / 12.0),
                    cy + ry * math.sin(t * math.pi / 12.0))
                   for t in range(25)]
        else:
            continue
        ln = sum(math.hypot(pts[i + 1][0] - pts[i][0],
                            pts[i + 1][1] - pts[i][1])
                 for i in range(len(pts) - 1))
        prims.append({"pts": pts, "len": ln})
    return _resample(prims)


def build_library(extra: dict | None = None) -> dict:
    """{key: {"label", "cat", "w_in", "d_in", "grid"}} — Loft stencils
    plus user-labeled custom symbols (``extra``, same shape but with
    raw ``pts_in`` instead of ops)."""
    lib = {}
    for key, spec in STENCILS.items():
        pts = _stencil_pts(spec)
        if not pts:
            continue
        g = _grid(pts, 0.0, False)
        text = next((str(op[3]) for op in spec["ops"] if op[0] == "t"), None)
        lib[key] = {"label": spec["label"], "cat": spec["cat"],
                    "w_in": spec["w_in"], "d_in": spec["d_in"],
                    "grid": g, "dgrid": _dilate(g), "text": text}
    for key, spec in (extra or {}).items():
        pts = [tuple(p) for p in spec.get("pts_in", [])]
        if len(pts) < MIN_PRIMS:
            continue
        g = _grid(pts, 0.0, False)
        lib[key] = {"label": spec.get("label", key), "cat": "custom",
                    "w_in": float(spec["w_in"]), "d_in": float(spec["d_in"]),
                    "grid": g, "dgrid": _dilate(g), "text": None}
    return lib


def make_symbol(cluster_pts, ppf: float, label: str) -> dict:
    """Turn a labeled unknown cluster into a custom library entry
    (points stored in real inches about the centroid — human-gated
    learning, the review-deck precedent)."""
    n = len(cluster_pts)
    mx = sum(p[0] for p in cluster_pts) / n
    my = sum(p[1] for p in cluster_pts) / n
    k = 12.0 / ppf
    pts_in = [((x - mx) * k, (y - my) * k) for x, y in cluster_pts]
    xs = [p[0] for p in pts_in]
    ys = [p[1] for p in pts_in]
    return {"label": label, "pts_in": pts_in,
            "w_in": max(xs) - min(xs), "d_in": max(ys) - min(ys)}


# --------------------------------------------------------------------------- #
#  the count                                                                  #
# --------------------------------------------------------------------------- #

def count_fixtures(page, ppf: float, extra_symbols: dict | None = None,
                   library: dict | None = None) -> dict:
    """Count fixture symbols on one vector page at a VERIFIED scale.

    ``ppf`` (points per real foot) must come from the Story Pole or a
    human calibration — size sanity is what separates a water closet
    from a water-closet-shaped detail blob."""
    if not ppf or ppf <= 0:
        raise ValueError("count_fixtures needs a verified pt/ft scale "
                         "(run the Story Pole first)")
    lib = library if library is not None else build_library(extra_symbols)
    from . import setscale
    doors = [(c["center"][0], c["center"][1], c["r_pt"])
             for c in setscale._door_candidates(page)]
    clusters, excluded = _clusters(_prims_from_page(page), ppf,
                                   door_centers=doors)
    words = [((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, w[4].strip().upper())
             for w in page.get_text("words")]

    def size_ok(spec, w_ft, h_ft):
        want = sorted((spec["w_in"] / 12.0, spec["d_in"] / 12.0))
        got = sorted((w_ft, h_ft))
        return all(abs(g - w) <= SIZE_TOL * w + 0.05
                   for g, w in zip(got, want)), got, want

    hits, unknown = [], []
    rejected_size = 0
    for cl in clusters:
        if len(cl) < MIN_PRIMS:
            continue
        allpts = [p for pr in cl for p in pr["pts"]]
        x0, y0, x1, y1 = _bbox(allpts)
        w_ft, h_ft = (x1 - x0) / ppf, (y1 - y0) / ppf
        if not (SIZE_MIN_FT <= max(w_ft, h_ft) <= SIZE_MAX_FT
                and min(w_ft, h_ft) >= SIZE_MIN_FT / 2):
            continue
        pts = _resample(cl)
        grids = _poses(pts)
        scored = sorted(
            ((max(_score(g, dg, spec["grid"], spec["dgrid"])
                  for g, dg in grids), key) for key, spec in lib.items()),
            reverse=True)
        best_sc, best_key = scored[0] if scored else (0.0, None)
        entry = {"bbox": (x0, y0, x1, y1),
                 "size_ft": (round(w_ft, 2), round(h_ft, 2)),
                 "nearest": best_key, "score": round(best_sc, 3)}
        if best_key is not None and best_sc >= MATCH_SCORE:
            spec = lib[best_key]
            ok, got, want = size_ok(spec, w_ft, h_ft)
            if not ok:
                rejected_size += 1
                entry["rejected"] = (
                    f"matches {best_key} at {best_sc:.2f} but footprints "
                    f"{got[0]:.1f}x{got[1]:.1f} ft (expected "
                    f"~{want[0]:.1f}x{want[1]:.1f})")
            else:
                # two size-sane candidates within the margin = AMBIGUOUS —
                # surfaced for the human, never resolved silently (some
                # conventions draw near-identical symbols, e.g. mop sink
                # vs single-bowl sink: concentric rectangles both)
                rival = next(
                    (k for sc, k in scored[1:]
                     if best_sc - sc <= AMBIG_MARGIN
                     and size_ok(lib[k], w_ft, h_ft)[0]), None)
                if rival is not None:
                    entry["ambiguous"] = (best_key, rival)
                    entry["rejected"] = (
                        f"ambiguous between {best_key} and {rival} "
                        f"({best_sc:.2f} vs a rival within "
                        f"{AMBIG_MARGIN}) — label it by hand")
                elif spec["text"] and not any(
                        t == spec["text"].upper()
                        and x0 - 3 <= cx <= x1 + 3 and y0 - 3 <= cy <= y1 + 3
                        for cx, cy, t in words):
                    # text-labeled stencils (WH, CO) are bare circles;
                    # the label IS the symbol — no label, no count
                    entry["rejected"] = (
                        f"shaped like {best_key} but its "
                        f"'{spec['text']}' label is missing")
                else:
                    entry["key"] = best_key
                    entry["label"] = spec["label"]
                    entry["cat"] = spec["cat"]
                    hits.append(entry)
                    continue
        if best_sc < NEAR_SCORE:
            entry["nearest"] = None
        entry["pts"] = pts
        unknown.append(entry)
    counts: dict = {}
    for h in hits:
        counts[h["key"]] = counts.get(h["key"], 0) + 1
    excluded["size_rejected"] = rejected_size
    return {"ppf": ppf, "counts": counts, "hits": hits, "unknown": unknown,
            "excluded": excluded}
