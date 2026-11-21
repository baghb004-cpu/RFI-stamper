"""Concrete reading: walls, columns, round columns and other solid
concrete from the wall FILL CLASS, through the Sightline.

Brief section 5.3.  The fill color is never hardcoded: it is learned from
the sheet (:func:`fill_classes` scores every fill color by how much
wall-like and column-like area it carries) and when two classes are
plausible the reader does not pick: it reports both, with picture crops,
and the level waits for the user's answer.

Every shape that comes back is a POLYGON at the face of concrete, exactly
the visible polygon the Sightline returned (the exact vector outline, the
exact vector clip, or the outline traced off the render, in that order of
confidence).  Nothing here ever falls back to a bounding box.

World feet out; the page polygon rides along for the picture check.
"""
from __future__ import annotations

import math
import re

from .. import geom2d as g2
from .. import sightline as sl
from . import model as M

COLOR_BIN = 8                 # fill colors within this (per channel) are one class
T_MIN_FT, T_MAX_FT = 0.25, 6.0        # plausible concrete thickness
PLAUSIBLE = 0.35              # a second class at this share of the best is "also plausible"
COL_MAX_FT = 5.0              # a rectangle up to this long (and compact) is a column
COMPACT = 2.5                 # length / width at or below this is compact
MARK_RE = re.compile(r"^[A-Z]{1,3}-?\d{1,2}[A-Z]?$")
MARK_REACH_FT = 3.5
OPEN_MIN_FT, OPEN_MAX_FT = 1.0, 20.0


# -------------------------------------------------------------- geometry --

def thickness_of(poly) -> float:
    """Thickness as drawn: the shortest inward crossing measured from the
    middle of any face at least as long as that crossing.  (A 12 ft x 1 ft
    wall crosses 1 ft from each long face; its end faces cross 12 ft and
    are shorter than that, so they do not vote.)"""
    pts = g2.dedupe(poly)
    n = len(pts)
    if n < 3:
        return 0.0
    ccw = g2.area(pts) > 0
    best = None
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln < 1e-9:
            continue
        ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
        nx, ny = (-uy, ux) if ccw else (uy, -ux)          # inward normal
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        d = _ray_hit((mx, my), (nx, ny), pts, skip=i)
        if d is None or d > ln + 1e-9:
            continue
        if best is None or d < best:
            best = d
    if best is None:
        return g2.min_area_rect(pts)["width"]
    return best


def _ray_hit(o, d, pts, skip: int):
    n = len(pts)
    best = None
    for j in range(n):
        if j == skip:
            continue
        p, q = pts[j], pts[(j + 1) % n]
        ex, ey = q[0] - p[0], q[1] - p[1]
        den = d[0] * ey - d[1] * ex
        if abs(den) < 1e-12:
            continue
        t = ((p[0] - o[0]) * ey - (p[1] - o[1]) * ex) / den
        u = ((p[0] - o[0]) * d[1] - (p[1] - o[1]) * d[0]) / den
        if t > 1e-9 and -1e-9 <= u <= 1 + 1e-9:
            if best is None or t < best:
                best = t
    return best


def end_faces(poly, thickness: float) -> list:
    """The short END faces of a wall polygon: faces about as long as the
    wall is thick, standing between two long faces.  Returns
    ``[{"a", "b", "mid", "inward"}]`` (``inward`` = unit vector into the
    wall along its run)."""
    pts = g2.dedupe(poly)
    n = len(pts)
    out = []
    if n < 4 or thickness <= 0:
        return out
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln > 1.25 * thickness or ln < 0.5 * thickness:
            continue
        prev_len = math.hypot(a[0] - pts[i - 1][0], a[1] - pts[i - 1][1])
        nb = pts[(i + 2) % n]
        next_len = math.hypot(nb[0] - b[0], nb[1] - b[1])
        if prev_len < 2.0 * thickness or next_len < 2.0 * thickness:
            continue
        # inward = along the neighbouring long faces, away from this face
        vx, vy = pts[i - 1][0] - a[0], pts[i - 1][1] - a[1]
        d = math.hypot(vx, vy)
        out.append({"a": a, "b": b, "mid": ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0),
                    "inward": (vx / d, vy / d)})
    return out


# ------------------------------------------------------- the fill class --

def _bin(rgb) -> tuple:
    return tuple(int(round(v / COLOR_BIN)) for v in rgb)


def fill_classes(fills, ppf: float, region=None) -> list:
    """Score every fill color on the sheet as a concrete class.

    ``region`` is a viewer-point box; shapes outside it (title block,
    legends) do not vote.  Returns ``[{"rgb", "count", "shapes", "area_ft2",
    "score"}]`` best first; ``score`` is the wall-like plus column-like
    area in square feet."""
    acc: dict = {}
    for f in fills:
        if f.status not in (sl.VISIBLE, sl.CLIPPED):
            continue
        for poly in f.polys:
            if len(poly) < 3:
                continue
            if region is not None:
                cx, cy = g2.centroid(poly)
                if not (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]):
                    continue
            a = abs(g2.area(poly)) / (ppf * ppf)
            t = thickness_of(poly) / ppf
            r = g2.min_area_rect(poly)
            length = r["length"] / ppf
            key = _bin(f.rgb)
            c = acc.setdefault(key, {"rgb": f.rgb, "count": 0, "shapes": 0,
                                     "area_ft2": 0.0, "score": 0.0, "_w": 0.0, "_sum": [0, 0, 0]})
            c["count"] += 1
            c["area_ft2"] += a
            c["_w"] += a
            for k in range(3):
                c["_sum"][k] += f.rgb[k] * a
            if T_MIN_FT <= t <= T_MAX_FT and length >= 1.0 and a >= 0.25:
                c["shapes"] += 1
                c["score"] += a
    out = []
    for c in acc.values():
        if c["_w"] > 0:
            c["rgb"] = tuple(int(round(v / c["_w"])) for v in c["_sum"])
        del c["_w"], c["_sum"]
        out.append(c)
    out.sort(key=lambda c: -c["score"])
    return out


def choose_class(classes, forced=None) -> tuple:
    """-> ``(rgb | None, verdict, candidates)``.

    ``verdict`` is ``"one"`` (a single plausible class), ``"forced"`` (the
    user's answer was applied), ``"ask"`` (two or more classes are
    plausible: the reader will not pick) or ``"none"``."""
    cands = [c for c in classes if c["score"] > 0]
    if forced is not None:
        want = tuple(int(v) for v in forced)
        for c in classes:
            if max(abs(a - b) for a, b in zip(c["rgb"], want)) <= 2 * COLOR_BIN:
                return c["rgb"], "forced", cands
        return None, "none", cands
    if not cands:
        return None, "none", cands
    plausible = [c for c in cands if c["score"] >= PLAUSIBLE * cands[0]["score"]]
    if len(plausible) > 1:
        return None, "ask", plausible
    return cands[0]["rgb"], "one", cands


def class_crops(render, fills, rgb, k: int = 3, pad_pt: float = 30.0) -> list:
    """Picture crops of the ``k`` largest shapes of a fill class, as PNG
    bytes (the "ask once, with picture crops" evidence)."""
    import fitz
    key = _bin(rgb)
    shapes = sorted(((abs(g2.area(p)), p) for f in fills if _bin(f.rgb) == key
                     for p in f.polys if len(p) >= 3), key=lambda t: -t[0])[:k]
    out = []
    for _a, poly in shapes:
        x0, y0, x1, y1 = g2.bbox(poly)
        (c0,), (r0,) = render.px([x0 - pad_pt], [y0 - pad_pt])
        (c1,), (r1,) = render.px([x1 + pad_pt], [y1 + pad_pt])
        c0, r0 = max(0, int(c0)), max(0, int(r0))
        c1, r1 = min(render.w, int(c1)), min(render.h, int(r1))
        if c1 - c0 < 4 or r1 - r0 < 4:
            continue
        crop = render.rgb[r0:r1, c0:c1]
        pix = fitz.Pixmap(fitz.csRGB, crop.shape[1], crop.shape[0],
                          crop.tobytes(), False)
        out.append(pix.tobytes("png"))
    return out


# ---------------------------------------------------------- the elements --

def _is_circle(poly) -> tuple | None:
    cx, cy, r, rms = g2.fit_circle(poly)
    if r <= 0 or rms > 0.03 * r:
        return None
    a = abs(g2.area(poly))
    if abs(a - math.pi * r * r) > 0.04 * math.pi * r * r:
        return None
    return (cx, cy, r)


def read_concrete(fills, frame: M.Frame, rgb, prov: M.Provenance) -> tuple:
    """Fills of the chosen class -> ``(elements, remarks)``.

    Elements are unnumbered (the reader assigns stable ids).  Remarks are
    plain reader lines for the run report."""
    key = _bin(rgb)
    out, remarks = [], []
    for f in fills:
        if _bin(f.rgb) != key or f.status not in (sl.VISIBLE, sl.CLIPPED):
            continue
        traced = f.method == "traced"
        for poly in f.polys:
            if len(poly) < 3:
                continue
            page_poly = g2.drop_collinear(poly, 0.02)
            world = [frame.to_world(x, y) for x, y in page_poly]
            p = M.Provenance(**prov.to_dict())
            p.method = M.TRACED if traced else M.VECTOR
            p.confidence = 0.7 if traced else (0.95 if f.status == sl.CLIPPED else 1.0)
            attrs = {"area_ft2": abs(g2.area(world)),
                     "area_vector_pt2": f.area_vector, "area_visible_pt2": f.area_visible}
            if f.status == sl.CLIPPED:
                attrs["clipped"] = f.why
            if f.obscured >= sl.OBSCURED_MIN:
                attrs["obscured"] = True
            circ = _is_circle(page_poly) if (f.curved and f.status == sl.VISIBLE) else None
            if circ is not None:
                c = frame.to_world(circ[0], circ[1])
                r = circ[2] * frame.ft_per_pt
                attrs["diameter_ft"] = 2.0 * r
                out.append(M.Element(kind=M.ROUND_COLUMN, poly=world, page_poly=page_poly,
                                     center=c, radius_ft=r, attrs=attrs, prov=p,
                                     sources=[M.SRC_STRUCT]))
                continue
            r = g2.min_area_rect(world)
            t = thickness_of(world)
            attrs.update(thickness_ft=t, length_ft=r["length"], axis=r["u"])
            if (g2.is_rectangle(world, 0.03) and r["length"] <= COL_MAX_FT
                    and r["length"] <= COMPACT * max(r["width"], 1e-9)):
                kind = M.COLUMN
            elif r["length"] >= COMPACT * max(t, 1e-9):
                kind = M.WALL
            else:
                kind = M.CONCRETE
            if f.curved and f.status != sl.VISIBLE:
                remarks.append("a curved concrete shape is partly hidden on the "
                               "plotted sheet and was read as a polygon")
            out.append(M.Element(kind=kind, poly=world, page_poly=page_poly, attrs=attrs,
                                 prov=p, sources=[M.SRC_STRUCT]))
    return out, remarks


def assign_marks(elements, words, frame: M.Frame, known_marks=()) -> list:
    """Wall marks as drawn: a mark word goes to the nearest wall within
    reach.  When two walls are about equally near the mark is NOT
    assigned (remark returned)."""
    remarks = []
    walls = [e for e in elements if e.kind == M.WALL]
    known = {m.upper() for m in known_marks}
    for w in words:
        tok = w[4].strip().upper()
        if not MARK_RE.match(tok):
            continue
        if known and tok not in known:
            continue
        if not known and not tok.startswith(("SW", "W", "CW")):
            continue
        pt = frame.to_world((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0)
        ranked = sorted(((g2.dist_point_poly(pt, e.poly), e) for e in walls),
                        key=lambda t: t[0])
        if not ranked or ranked[0][0] > MARK_REACH_FT:
            continue
        if len(ranked) > 1 and ranked[1][0] <= 1.25 * ranked[0][0] + 0.05 \
                and ranked[1][1].attrs.get("run") != ranked[0][1].attrs.get("run", -1):
            remarks.append(f"mark {tok} sits between two walls and was not assigned")
            continue
        ranked[0][1].mark = tok
    return remarks


def link_runs(elements, strokes, frame: M.Frame) -> list:
    """Collinear wall pieces of one thickness form a RUN; a gap between
    two pieces is an OPENING only when linework spans it along both faces
    (the header lines).  Adds ``attrs["run"]`` to walls and returns the
    new ``wall_opening`` elements."""
    walls = [e for e in elements if e.kind == M.WALL and g2.is_rectangle(e.poly, 0.03)]
    run_id = 0
    for e in elements:
        if e.kind == M.WALL:
            e.attrs["run"] = run_id
            run_id += 1
    segs = []
    for st in strokes:
        if st.curve is None and len(st.pts) == 2:
            segs.append((frame.to_world(*st.pts[0]), frame.to_world(*st.pts[1])))
    openings = []
    used = set()
    order = sorted(walls, key=lambda e: (round(e.attrs["axis"][0], 3), e.centroid()))
    for i, a in enumerate(order):
        ra = g2.min_area_rect(a.poly)
        ux, uy = ra["u"]
        for b in order[i + 1:]:
            rb = g2.min_area_rect(b.poly)
            if abs(ux * rb["u"][0] + uy * rb["u"][1]) < math.cos(math.radians(1.0)):
                continue
            if abs(a.attrs["thickness_ft"] - b.attrs["thickness_ft"]) > 1.0 * M.IN:
                continue
            dx, dy = rb["center"][0] - ra["center"][0], rb["center"][1] - ra["center"][1]
            if abs(-dx * uy + dy * ux) > 1.0 * M.IN:
                continue                                  # not on one line
            along = dx * ux + dy * uy
            gap = abs(along) - ra["length"] / 2.0 - rb["length"] / 2.0
            if not (OPEN_MIN_FT <= gap <= OPEN_MAX_FT):
                continue
            s = 1.0 if along > 0 else -1.0
            ja = (ra["center"][0] + s * ux * ra["length"] / 2.0,
                  ra["center"][1] + s * uy * ra["length"] / 2.0)
            jb = (rb["center"][0] - s * ux * rb["length"] / 2.0,
                  rb["center"][1] - s * uy * rb["length"] / 2.0)
            # another wall piece standing in the gap means this is not one opening
            if any(o is not a and o is not b and o.attrs.get("run") is not None
                   and g2.dist_point_seg(o.centroid(), ja, jb) < 0.5 * a.attrs["thickness_ft"]
                   for o in walls):
                continue
            t = a.attrs["thickness_ft"] / 2.0
            nx, ny = -uy, ux
            faces = [((ja[0] + nx * t, ja[1] + ny * t), (jb[0] + nx * t, jb[1] + ny * t)),
                     ((ja[0] - nx * t, ja[1] - ny * t), (jb[0] - nx * t, jb[1] - ny * t))]
            cover = [_coverage(fa, fb, segs) for fa, fb in faces]
            key = tuple(sorted((id(a), id(b))))
            if key in used:
                continue
            if min(cover) < 0.5:
                continue                                  # a gap between two walls, not an opening
            used.add(key)
            b.attrs["run"] = a.attrs["run"]
            poly = [faces[0][0], faces[0][1], faces[1][1], faces[1][0]]
            prov = M.Provenance(**a.prov.to_dict())
            openings.append(M.Element(
                kind=M.WALL_OPENING, poly=poly,
                page_poly=[frame.to_page(*p) for p in poly],
                attrs={"width_ft": gap, "jamb_a": ja, "jamb_b": jb, "axis": (ux, uy),
                       "thickness_ft": a.attrs["thickness_ft"],
                       "center": ((ja[0] + jb[0]) / 2.0, (ja[1] + jb[1]) / 2.0),
                       "hosts": (id(a), id(b))},
                prov=prov, sources=[M.SRC_STRUCT]))
    # marks ride the whole run
    return openings


def _coverage(a, b, segs, tol: float = 1.5 * M.IN) -> float:
    """Share of a-b covered by collinear visible linework."""
    ln = math.hypot(b[0] - a[0], b[1] - a[1])
    if ln <= 0:
        return 0.0
    ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
    iv = []
    for p, q in segs:
        if g2.dist_point_line(p, a, b) > tol or g2.dist_point_line(q, a, b) > tol:
            continue
        t0 = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
        t1 = (q[0] - a[0]) * ux + (q[1] - a[1]) * uy
        lo, hi = max(0.0, min(t0, t1)), min(ln, max(t0, t1))
        if hi > lo:
            iv.append((lo, hi))
    iv.sort()
    tot, end = 0.0, 0.0
    for lo, hi in iv:
        lo = max(lo, end)
        if hi > lo:
            tot += hi - lo
            end = hi
    return tot / ln


def spread_marks(elements) -> None:
    """Every piece of a run carries the run's mark."""
    by_run: dict = {}
    for e in elements:
        if e.kind == M.WALL and e.mark:
            by_run.setdefault(e.attrs.get("run"), e.mark)
    for e in elements:
        if e.kind == M.WALL and not e.mark and e.attrs.get("run") in by_run:
            e.mark = by_run[e.attrs["run"]]


def shear_wall_ends(elements, openings, shear_marks) -> list:
    """Ends and jambs of every shear wall -> unnumbered ``sw_end``
    elements.  An end face that borders an opening is a JAMB."""
    marks = {m.upper() for m in shear_marks}
    out = []
    for e in elements:
        if e.kind != M.WALL or not e.mark or e.mark.upper() not in marks:
            continue
        t = e.attrs.get("thickness_ft") or thickness_of(e.poly)
        for face in end_faces(e.poly, t):
            role, opening = "end", None
            for o in openings:
                for j in (o.attrs["jamb_a"], o.attrs["jamb_b"]):
                    if math.hypot(face["mid"][0] - j[0], face["mid"][1] - j[1]) <= 2.0 * M.IN:
                        role, opening = "jamb", o
            out.append(M.Element(
                kind=M.SW_END, center=face["mid"], mark=e.mark,
                poly=[face["a"], face["b"]], closed=False,
                page_poly=[],
                attrs={"role": role, "host": e, "opening": opening,
                       "inward": face["inward"], "face_a": face["a"], "face_b": face["b"],
                       "thickness_ft": t},
                prov=M.Provenance(**e.prov.to_dict()), sources=list(e.sources)))
    return out
