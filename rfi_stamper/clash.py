"""Clash-Lite: deterministic interference checks over a Loft model.

Pipe runs vs walls and pipe vs pipe, computed exactly — capsules against
oriented boxes and capsules against capsules, no meshes, no BVH, no
sampling luck.  Consumed by the Backcheck (findings) and the 3D viewer
(clash pins).  Pure stdlib math, GUI-free, offline.

The vocabulary is standard coordination practice:

* **hard** — two objects occupy the same volume (overlap past the
  ignore-below threshold).  Escalates when the bury is gross.
* **clearance** — a gap thinner than the opt-in buffer (off by default:
  per-discipline clearance codes are SKIPPED, and any nonzero default
  would flag tight-but-legal layouts).
* **penetration** — a pipe crossing a wall transversely is SUPPOSED to
  happen (it gets a sleeve/firestop); flagging it as a hard clash buries
  real problems.  A degree-1 stub ending inside a wall (fixture rough-in)
  lands here too.
* **concealed / wontfit** — a run buried lengthwise inside a wall:
  verify-cavity info when it fits, major when the diameter physically
  cannot (dia >= wall thickness).
* **duplicate** — the same run modeled twice (same system, near-parallel,
  near-coaxial, overlapping); one info hit, suppressing the hard spam.

False-positive discipline (the "zero on a clean model" contract): runs
joined at a network node never clash at their shared fitting (adjacency
exclusion); overlaps below HARD_IGNORE_IN are noise, not findings; runs
with no invert have no real elevation and are HONESTLY EXCLUDED and
counted — never dropped silently (stats["no_elevation"]).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

_EPS = 1e-9

# ------------------------------------------------------- tolerance table ---
#: ignore-below threshold on hard OVERLAP (never raw distance), inches —
#: the common coordination ignore-below convention; verify against the
#: project coordination plan.
HARD_IGNORE_IN = 0.5
#: opt-in soft-clash buffer, inches; 0 = off.  Per-discipline clearance
#: tables (working space, insulation, access) are SKIPPED — one global
#: knob only; 1-2 in is a common choice for insulated lines.
CLEARANCE_IN = 0.0
#: wall body height, feet — matches draft.to_bim's default.
WALL_HEIGHT_FT = 10.0
#: duplicate = same-system near-coincident modeling: included angle,
#: axis separation (vs min radius) and extent-overlap fraction.
DUP_ANGLE_DEG = 2.0
DUP_OVERLAP = 0.5
#: ternary-search iterations: (2/3)^60 bracket ~ 3e-11 — machine
#: precision on a convex function, deterministic.
TERN_ITERS = 60

#: kind -> Backcheck severity (hard may escalate to blocker; see severity()).
KIND_SEVERITY = {"hard": "major", "wontfit": "major", "clearance": "minor",
                 "penetration": "info", "concealed": "info",
                 "duplicate": "info"}


# ------------------------------------------------------- geometry kernel ---

def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def seg_seg(p1, q1, p2, q2):
    """Closest points of two 3D segments -> (dist, s, t, c1, c2).  The
    standard closed form; handles point-degenerate and parallel segments
    (parallel picks s = 0 — one deterministic answer every run)."""
    d1 = (q1[0] - p1[0], q1[1] - p1[1], q1[2] - p1[2])
    d2 = (q2[0] - p2[0], q2[1] - p2[1], q2[2] - p2[2])
    r = (p1[0] - p2[0], p1[1] - p2[1], p1[2] - p2[2])
    a = d1[0] * d1[0] + d1[1] * d1[1] + d1[2] * d1[2]
    e = d2[0] * d2[0] + d2[1] * d2[1] + d2[2] * d2[2]
    f = d2[0] * r[0] + d2[1] * r[1] + d2[2] * r[2]
    if a <= _EPS and e <= _EPS:
        s = t = 0.0
    elif a <= _EPS:
        s, t = 0.0, _clamp01(f / e)
    else:
        c = d1[0] * r[0] + d1[1] * r[1] + d1[2] * r[2]
        if e <= _EPS:
            t, s = 0.0, _clamp01(-c / a)
        else:
            b = d1[0] * d2[0] + d1[1] * d2[1] + d1[2] * d2[2]
            den = a * e - b * b
            s = _clamp01((b * f - c * e) / den) if den > _EPS else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, _clamp01(-c / a)
            elif t > 1.0:
                t, s = 1.0, _clamp01((b - c) / a)
    c1 = (p1[0] + s * d1[0], p1[1] + s * d1[1], p1[2] + s * d1[2])
    c2 = (p2[0] + t * d2[0], p2[1] + t * d2[1], p2[2] + t * d2[2])
    return (math.dist(c1, c2), s, t, c1, c2)


def sd_box(p, lo, hi) -> float:
    """Exact Euclidean SIGNED distance from a point to an axis box:
    positive outside, negative = -(interior depth to the nearest face)."""
    q = (max(lo[0] - p[0], p[0] - hi[0]),
         max(lo[1] - p[1], p[1] - hi[1]),
         max(lo[2] - p[2], p[2] - hi[2]))
    if q[0] > 0.0 or q[1] > 0.0 or q[2] > 0.0:
        return math.sqrt(max(q[0], 0.0) ** 2 + max(q[1], 0.0) ** 2
                         + max(q[2], 0.0) ** 2)
    return max(q)


def ternary_min(fn, lo: float = 0.0, hi: float = 1.0,
                iters: int = TERN_ITERS):
    """Deterministic fixed-iteration ternary search for the minimum of a
    CONVEX function on [lo, hi] -> (t, fn(t)).  The signed distance of a
    convex set along an affine segment is convex, so this replaces a page
    of Voronoi-region case analysis.  NEVER reuse on a union of boxes —
    the min of convex functions is not convex; run it per box."""
    for _ in range(iters):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if fn(m1) <= fn(m2):
            hi = m2
        else:
            lo = m1
    t = (lo + hi) / 2.0
    return t, fn(t)


# ---------------------------------------------------------- scene sources ---

@dataclass
class Capsule:
    """One pipe polyline segment as a centerline capsule (axis a->b,
    radius r, all feet).  z is the CENTERLINE: run_z gives the invert
    (pipe bottom), lifted +r here."""
    ent_id: str
    system: str
    dia_in: float
    seg_ix: int
    a: tuple
    b: tuple
    r: float
    end_a: bool = False         # a/b is a run-terminal vertex
    end_b: bool = False


@dataclass
class WallBox:
    """One wall as an oriented box: local frame u (centerline), n
    (normal), origin at endpoint a; body = [0,L] x [-half,half] x [z0,H].
    Slabs would be the same primitive — the math is slab-ready, the data
    source isn't (see the SKIP list)."""
    ent_id: str
    a: tuple
    u: tuple
    n: tuple
    half: float
    length: float
    thick_in: float
    height: float = WALL_HEIGHT_FT

    def to_local(self, p) -> tuple:
        dx, dy = p[0] - self.a[0], p[1] - self.a[1]
        return (dx * self.u[0] + dy * self.u[1],
                dx * self.n[0] + dy * self.n[1], p[2])


def capsules(model):
    """(capsules, stats) — every polyline segment of every pipe run that
    carries an invert, as centerline capsules.  z from pipewright.run_z
    (the SAME profile the viewer draws) + r.  Runs with no invert (all
    pressure systems today) have no real elevation — including them at
    z = 0 would stack every system into one plane and produce a clash
    storm; they are excluded and counted in stats["no_elevation"]."""
    from . import pipewright
    caps, no_elev = [], 0
    for e in pipewright._pipes(model):
        if e.props.get("invert_ft") is None:
            no_elev += 1
            continue
        zs = pipewright.run_z(e)
        dia = float(e.props.get("dia_in", 4.0))
        r = dia / 24.0
        system = str(e.props.get("system", "san"))
        last = len(e.pts) - 2
        for i in range(len(e.pts) - 1):
            p, q = e.pts[i], e.pts[i + 1]
            caps.append(Capsule(
                e.id, system, dia, i,
                (float(p[0]), float(p[1]), zs[i] + r),
                (float(q[0]), float(q[1]), zs[i + 1] + r), r,
                end_a=(i == 0), end_b=(i == last)))
    return caps, {"no_elevation": no_elev, "capsules": len(caps)}


def wall_boxes(model, wall_height: float = WALL_HEIGHT_FT) -> list:
    """Every well-formed wall as a WallBox (backcheck._walls frames —
    the one wall-frame source)."""
    from .backcheck import _walls
    return [WallBox(e.id, a, u, n, half, ln,
                    float(e.props.get("thick_in", 4.75)),
                    float(wall_height))
            for e, a, _b, u, n, half, ln in _walls(model)]


# ----------------------------------------------------------------- detect ---

@dataclass
class ClashHit:
    """One raw pairwise hit.  overlap_ft is signed: positive = bury depth
    (hard/penetration/concealed), negative = clear gap (clearance)."""
    kind: str
    ent_a: str
    ent_b: str
    system_a: str
    system_b: str
    dia_a: float
    dia_b: float
    overlap_ft: float
    at: tuple                   # (x, y, z) witness
    seg_ix: tuple = (0, 0)


def _adjacent_pairs(model) -> set:
    """frozenset(ent_a, ent_b) for runs that share a network node —
    connected pipes always 'clash' at their fitting by construction."""
    from . import pipewright
    out = set()
    for node in pipewright.network(model).nodes:
        ids = sorted({leg.ent_id for leg in node.legs})
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                out.add(frozenset((ids[i], ids[j])))
    return out


def _end_degrees(model) -> dict:
    """{(ent_id, "first"|"last"): node degree} for run-terminal vertices."""
    from . import pipewright
    out = {}
    for node in pipewright.network(model).nodes:
        for leg in node.legs:
            if leg.end in ("first", "last"):
                out[(leg.ent_id, leg.end)] = node.degree
    return out


def _aabb(c: Capsule, pad: float) -> tuple:
    return (min(c.a[0], c.b[0]) - pad, min(c.a[1], c.b[1]) - pad,
            min(c.a[2], c.b[2]) - pad, max(c.a[0], c.b[0]) + pad,
            max(c.a[1], c.b[1]) + pad, max(c.a[2], c.b[2]) + pad)


def _aabb_miss(b1, b2) -> bool:
    return (b1[3] < b2[0] or b2[3] < b1[0] or b1[4] < b2[1]
            or b2[4] < b1[1] or b1[5] < b2[2] or b2[5] < b1[2])


def _duplicate(A: Capsule, B: Capsule, dist: float) -> bool:
    """Same-system near-parallel near-coaxial overlapping pair — the same
    element modeled twice."""
    if A.system != B.system:
        return False
    va = (A.b[0] - A.a[0], A.b[1] - A.a[1], A.b[2] - A.a[2])
    vb = (B.b[0] - B.a[0], B.b[1] - B.a[1], B.b[2] - B.a[2])
    la = math.sqrt(va[0] ** 2 + va[1] ** 2 + va[2] ** 2)
    lb = math.sqrt(vb[0] ** 2 + vb[1] ** 2 + vb[2] ** 2)
    if la < _EPS or lb < _EPS:
        return False
    cosang = abs(va[0] * vb[0] + va[1] * vb[1] + va[2] * vb[2]) / (la * lb)
    if cosang < math.cos(math.radians(DUP_ANGLE_DEG)) - 1e-6:
        return False
    if dist >= min(A.r, B.r):           # not near-coaxial
        return False
    # projected extent overlap on A's axis, as a fraction of the shorter
    ua = (va[0] / la, va[1] / la, va[2] / la)

    def proj(p):
        return ((p[0] - A.a[0]) * ua[0] + (p[1] - A.a[1]) * ua[1]
                + (p[2] - A.a[2]) * ua[2])

    t0, t1 = sorted((proj(B.a), proj(B.b)))
    overlap = min(la, t1) - max(0.0, t0)
    return overlap >= DUP_OVERLAP * min(la, lb)


def detect(model, *, wall_height: float = None,
           hard_ignore_in: float = None, clearance_in: float = None):
    """(hits, stats) — the full deterministic clash pass.  Knobs default
    to the module constants AT CALL TIME (a project may set
    clash.CLEARANCE_IN and it sticks).  Broad phase is a flat double loop
    with an inflated-AABB reject (a Loft model holds tens of walls and at
    most a few hundred pipe segments — microseconds; past ~2000 segments
    the escape hatch is the floor-cell hash idiom of
    pipewright.network.node_at, NOT sweep-and-prune).  i < j pair order
    keeps the output deterministic and duplicate-free."""
    wall_height = WALL_HEIGHT_FT if wall_height is None else wall_height
    hard_ignore_in = (HARD_IGNORE_IN if hard_ignore_in is None
                      else hard_ignore_in)
    clearance_in = CLEARANCE_IN if clearance_in is None else clearance_in
    caps, stats = capsules(model)
    walls = wall_boxes(model, wall_height)
    stats["walls"] = len(walls)
    hits: list = []
    if not caps:
        return hits, stats
    adj = _adjacent_pairs(model)
    degrees = _end_degrees(model)
    clear_ft = max(float(clearance_in), 0.0) / 12.0
    hard_ft = float(hard_ignore_in) / 12.0

    # ---- pipe vs pipe: capsule against capsule -------------------------
    dup_pairs = set()
    boxes = [_aabb(c, c.r + clear_ft + hard_ft) for c in caps]
    for i in range(len(caps)):
        A = caps[i]
        for j in range(i + 1, len(caps)):
            B = caps[j]
            if A.ent_id == B.ent_id:            # self-clash: fittings
                continue
            pair = frozenset((A.ent_id, B.ent_id))
            if pair in adj:                     # joined runs: exclusion
                continue
            if _aabb_miss(boxes[i], boxes[j]):
                continue
            dist, _s, _t, c1, c2 = seg_seg(A.a, A.b, B.a, B.b)
            witness = ((c1[0] + c2[0]) / 2.0, (c1[1] + c2[1]) / 2.0,
                       (c1[2] + c2[2]) / 2.0)
            overlap = (A.r + B.r) - dist
            if _duplicate(A, B, dist):          # subsumes the hard hit
                if pair not in dup_pairs:
                    dup_pairs.add(pair)
                    hits.append(ClashHit("duplicate", A.ent_id, B.ent_id,
                                         A.system, B.system, A.dia_in,
                                         B.dia_in, overlap, witness,
                                         (A.seg_ix, B.seg_ix)))
                continue
            if overlap * 12.0 >= hard_ignore_in - 1e-9:
                hits.append(ClashHit("hard", A.ent_id, B.ent_id,
                                     A.system, B.system, A.dia_in, B.dia_in,
                                     overlap, witness,
                                     (A.seg_ix, B.seg_ix)))
            elif clear_ft > 0.0 and -overlap < clear_ft:
                hits.append(ClashHit("clearance", A.ent_id, B.ent_id,
                                     A.system, B.system, A.dia_in, B.dia_in,
                                     overlap, witness,
                                     (A.seg_ix, B.seg_ix)))

    # ---- pipe vs wall: capsule against oriented box.  Hard conflicts +
    # classification ONLY — pipes legitimately touch, enter and cross
    # walls, so soft wall clearance is meaningless (SKIP list).
    for cap in caps:
        for wb in walls:
            la = wb.to_local(cap.a)
            lb = wb.to_local(cap.b)
            lo = (0.0, -wb.half, 0.0)
            hi = (wb.length, wb.half, wb.height)
            pad = cap.r + hard_ft
            if any(max(la[k], lb[k]) < lo[k] - pad
                   or min(la[k], lb[k]) > hi[k] + pad for k in range(3)):
                continue                        # inflated-AABB reject

            def fdist(t, _la=la, _lb=lb, _lo=lo, _hi=hi):
                return sd_box((_la[0] + t * (_lb[0] - _la[0]),
                               _la[1] + t * (_lb[1] - _la[1]),
                               _la[2] + t * (_lb[2] - _la[2])), _lo, _hi)

            tmin, dmin = ternary_min(fdist)
            overlap = cap.r - dmin
            if overlap * 12.0 < hard_ignore_in - 1e-9:
                continue
            wx = cap.a[0] + tmin * (cap.b[0] - cap.a[0])
            wy = cap.a[1] + tmin * (cap.b[1] - cap.a[1])
            wz = cap.a[2] + tmin * (cap.b[2] - cap.a[2])
            witness = (wx, wy, wz)
            n0, n1 = la[1], lb[1]
            kind = "hard"
            if n0 * n1 < 0.0 and abs(n0) > wb.half and abs(n1) > wb.half:
                tc = n0 / (n0 - n1)             # in one face, out the other
                along = la[0] + tc * (lb[0] - la[0])
                zc = la[2] + tc * (lb[2] - la[2])
                if (-cap.r <= along <= wb.length + cap.r
                        and -cap.r <= zc <= wb.height + cap.r):
                    kind = "penetration"        # transverse: sleeve, not clash
            elif abs(n0) <= wb.half + _EPS and abs(n1) <= wb.half + _EPS:
                kind = ("wontfit" if cap.dia_in >= wb.thick_in - 1e-9
                        else "concealed")       # runs concealed in the wall
            else:
                # a degree-1 stub ENDING inside the wall body is a fixture
                # rough-in — normal; demote to the penetration bucket
                for pt, is_end, which in ((la, cap.end_a, "first"),
                                          (lb, cap.end_b, "last")):
                    if (is_end and abs(pt[1]) <= wb.half + _EPS
                            and -_EPS <= pt[0] <= wb.length + _EPS
                            and -_EPS <= pt[2] <= wb.height + _EPS
                            and degrees.get((cap.ent_id, which), 0) <= 1):
                        kind = "penetration"
                        break
            hits.append(ClashHit(kind, cap.ent_id, wb.ent_id, cap.system,
                                 "wall", cap.dia_in, wb.thick_in, overlap,
                                 witness, (cap.seg_ix, 0)))
    return hits, stats


# ------------------------------------------------------------- clustering ---

@dataclass
class ClashGroup:
    """One reportable issue: all hits of one kind between one unordered
    entity pair, carrying the count and the WORST hit — the industry
    'one issue per clash cluster, not per-segment spam' norm (adjacent-
    segment repeats along two snaking runs merge automatically)."""
    kind: str
    ent_a: str
    ent_b: str
    system_a: str
    system_b: str
    dia_a: float
    dia_b: float
    count: int
    overlap_ft: float
    at: tuple


_SEV_ORDER = {"blocker": 0, "major": 1, "minor": 2, "info": 3}


def severity(group: ClashGroup) -> str:
    """Backcheck severity for a group: hard escalates to blocker when the
    bury reaches half the smaller diameter — a gross bury outranks a
    graze (gravity systems cannot re-slope freely)."""
    sev = KIND_SEVERITY[group.kind]
    if group.kind == "hard" and group.overlap_ft * 12.0 >= \
            0.5 * min(group.dia_a, group.dia_b) - 1e-9:
        sev = "blocker"
    return sev


def group(hits) -> list:
    """Cluster raw hits into one ClashGroup per (kind, unordered entity
    pair), sorted (severity, -overlap, ent_a, ent_b) — deterministic."""
    bykey: dict = {}
    for h in hits:
        ea, eb = sorted((h.ent_a, h.ent_b))
        key = (h.kind, ea, eb)
        got = bykey.get(key)
        if got is None or h.overlap_ft > got.overlap_ft:
            flip = ea != h.ent_a
            g = ClashGroup(
                h.kind, ea, eb,
                h.system_b if flip else h.system_a,
                h.system_a if flip else h.system_b,
                h.dia_b if flip else h.dia_a,
                h.dia_a if flip else h.dia_b,
                (got.count if got else 0) + 1, h.overlap_ft, h.at)
            bykey[key] = g
        else:
            got.count += 1
    out = list(bykey.values())
    out.sort(key=lambda g: (_SEV_ORDER[severity(g)], -g.overlap_ft,
                            g.ent_a, g.ent_b))
    return out


def pins(groups) -> list:
    """[(x, y, z, label, color)] for Bim3DViewer.set_pins — C1, C2, ... in
    severity order, colored by Backcheck severity.  Zero new viewer
    machinery: pins already render stem + glowing head + label."""
    from .backcheck import SEVERITY_COLORS
    return [(g.at[0], g.at[1], g.at[2], f"C{i}",
             SEVERITY_COLORS[severity(g)])
            for i, g in enumerate(groups, 1)]
