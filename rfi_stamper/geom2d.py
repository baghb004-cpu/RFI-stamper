"""geom2d: the in-house 2D geometry kit (numpy + stdlib, no new packages).

Polygon area / containment, Sutherland-Hodgman clipping against a convex
clip, scanline rasterizing, square-element morphology on integral images,
boundary tracing with sub-pixel edge refitting, Douglas-Peucker
simplification, least-squares circle fitting and the minimum-area
rectangle.  The Sightline measures vector elements against a render with
these; the Batterboard readers build faces, corners and curves on them.

Conventions: a polygon is a list of ``(x, y)`` pairs, closed implicitly
(the last vertex connects back to the first).  Nothing here cares whether
y runs up or down; signed areas flip sign with the axis and callers that
care take ``abs``.  Raster helpers work in pixel coordinates with pixel
CENTERS at half-integers (pixel ``(row, col)`` covers ``[col, col+1) x
[row, row+1)``).
"""
from __future__ import annotations

import math

import numpy as np


# ------------------------------------------------------------- polygons --

def area(poly) -> float:
    """Signed shoelace area."""
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def centroid(poly) -> tuple:
    """Area centroid (vertex mean for a degenerate polygon)."""
    a = area(poly)
    n = len(poly)
    if n == 0:
        return (0.0, 0.0)
    if abs(a) < 1e-12:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    cx = cy = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        w = x0 * y1 - x1 * y0
        cx += (x0 + x1) * w
        cy += (y0 + y1) * w
    return (cx / (6.0 * a), cy / (6.0 * a))


def bbox(poly) -> tuple:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def perimeter(poly) -> float:
    n = len(poly)
    return sum(math.hypot(poly[(i + 1) % n][0] - poly[i][0],
                          poly[(i + 1) % n][1] - poly[i][1])
               for i in range(n))


def dedupe(poly, tol: float = 1e-9) -> list:
    """Drop consecutive duplicate vertices (and a closing duplicate)."""
    out = []
    for p in poly:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > tol:
            out.append((float(p[0]), float(p[1])))
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0],
                                   out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def drop_collinear(poly, tol: float = 1e-6) -> list:
    """Remove vertices that sit on the straight line between their
    neighbours (``tol`` = max perpendicular offset)."""
    pts = dedupe(poly)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        n = len(pts)
        for i in range(n):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
            if dist_point_line(b, a, c) <= tol and _between(a, b, c):
                pts.pop(i)
                changed = True
                break
    return pts


def _between(a, b, c) -> bool:
    return ((b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])) >= 0


def is_convex(poly, eps: float = 1e-9) -> bool:
    n = len(poly)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        cx, cy = poly[(i + 2) % n]
        cr = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cr) <= eps:
            continue
        s = 1 if cr > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return sign != 0


def clip_convex(subject, clip) -> list:
    """Sutherland-Hodgman: ``subject`` clipped to the CONVEX polygon
    ``clip`` (either winding).  Returns ``[]`` when nothing is left."""
    if len(subject) < 3 or len(clip) < 3:
        return []
    ccw = area(clip) > 0
    out = [(float(x), float(y)) for x, y in subject]
    n = len(clip)
    for i in range(n):
        ax, ay = clip[i]
        bx, by = clip[(i + 1) % n]
        if not out:
            break
        inp, out = out, []

        def side(p):
            v = (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)
            return v if ccw else -v

        prev = inp[-1]
        sp = side(prev)
        for cur in inp:
            sc = side(cur)
            if sc >= 0:
                if sp < 0:
                    out.append(_edge_cut(prev, cur, sp, sc))
                out.append(cur)
            elif sp >= 0:
                out.append(_edge_cut(prev, cur, sp, sc))
            prev, sp = cur, sc
    out = dedupe(out, 1e-9)
    return out if len(out) >= 3 and abs(area(out)) > 1e-12 else []


def _edge_cut(p, q, sp, sq):
    t = sp / (sp - sq)
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def point_in_poly(x: float, y: float, poly) -> bool:
    """Even-odd containment."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def points_in_poly(xs, ys, poly) -> np.ndarray:
    """Vectorized even-odd containment."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    inside = np.zeros(xs.shape, dtype=bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if yi != yj:
            cond = (yi > ys) != (yj > ys)
            xc = (xj - xi) * (ys - yi) / (yj - yi) + xi
            inside ^= cond & (xs < xc)
        j = i
    return inside


def dist_point_line(p, a, b) -> float:
    """Perpendicular distance from ``p`` to the infinite line a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy)
    if d < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(dx * (p[1] - a[1]) - dy * (p[0] - a[0])) / d


def dist_point_seg(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    if d2 < 1e-18:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def dist_point_poly(p, poly) -> float:
    """Distance from ``p`` to the polygon BOUNDARY."""
    n = len(poly)
    return min(dist_point_seg(p, poly[i], poly[(i + 1) % n]) for i in range(n))


def seg_intersection(a1, a2, b1, b2, eps: float = 1e-12):
    """Proper segment-segment intersection point, or None (parallel and
    collinear pairs return None)."""
    rx, ry = a2[0] - a1[0], a2[1] - a1[1]
    sx, sy = b2[0] - b1[0], b2[1] - b1[1]
    den = rx * sy - ry * sx
    if abs(den) <= eps * max(1.0, math.hypot(rx, ry) * math.hypot(sx, sy)):
        return None
    qx, qy = b1[0] - a1[0], b1[1] - a1[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return (a1[0] + rx * t, a1[1] + ry * t)
    return None


def line_intersection(a1, a2, b1, b2):
    """Infinite-line intersection, or None when parallel."""
    rx, ry = a2[0] - a1[0], a2[1] - a1[1]
    sx, sy = b2[0] - b1[0], b2[1] - b1[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None
    t = ((b1[0] - a1[0]) * sy - (b1[1] - a1[1]) * sx) / den
    return (a1[0] + rx * t, a1[1] + ry * t)


def hull(points) -> list:
    """Convex hull (Andrew monotone chain), counter-clockwise in a y-up
    frame."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lo = []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    up = []
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def min_area_rect(poly) -> dict:
    """Minimum-area enclosing rectangle of a point set.

    Returns ``{"center", "u" (unit vector along the LONG side), "length",
    "width", "corners"}``."""
    h = hull(poly)
    if len(h) < 2:
        p = h[0] if h else (0.0, 0.0)
        return {"center": p, "u": (1.0, 0.0), "length": 0.0, "width": 0.0,
                "corners": [p, p, p, p]}
    best = None
    n = len(h)
    for i in range(n):
        ax, ay = h[i]
        bx, by = h[(i + 1) % n]
        d = math.hypot(bx - ax, by - ay)
        if d < 1e-12:
            continue
        ux, uy = (bx - ax) / d, (by - ay) / d
        us = [p[0] * ux + p[1] * uy for p in h]
        vs = [-p[0] * uy + p[1] * ux for p in h]
        w, t = max(us) - min(us), max(vs) - min(vs)
        if best is None or w * t < best[0] - 1e-12:
            best = (w * t, ux, uy, min(us), max(us), min(vs), max(vs))
    _a, ux, uy, u0, u1, v0, v1 = best
    length, width = u1 - u0, v1 - v0
    cu, cv = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    center = (cu * ux - cv * uy, cu * uy + cv * ux)
    corners = [(u * ux - v * uy, u * uy + v * ux)
               for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    if width > length:
        length, width = width, length
        ux, uy = -uy, ux
    return {"center": center, "u": (ux, uy), "length": length,
            "width": width, "corners": corners}


def is_rectangle(poly, tol: float) -> bool:
    """True when ``poly`` is a 4-gon whose vertices sit within ``tol`` of
    its minimum-area rectangle's corners."""
    pts = drop_collinear(poly, tol)
    if len(pts) != 4:
        return False
    r = min_area_rect(pts)
    return all(min(math.hypot(p[0] - c[0], p[1] - c[1]) for c in r["corners"])
               <= tol for p in pts)


# --------------------------------------------------------------- curves --

def bezier(p0, c1, c2, p1, n: int = 12) -> list:
    """``n + 1`` points along a cubic Bezier, ends included."""
    out = []
    for i in range(n + 1):
        t = i / n
        m = 1.0 - t
        a, b, c, d = m * m * m, 3 * m * m * t, 3 * m * t * t, t * t * t
        out.append((a * p0[0] + b * c1[0] + c * c2[0] + d * p1[0],
                    a * p0[1] + b * c1[1] + c * c2[1] + d * p1[1]))
    return out


def bezier_split(p0, c1, c2, p1, t0: float, t1: float) -> tuple:
    """Control points of the sub-curve over ``[t0, t1]`` (de Casteljau)."""
    def lerp(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def cut(q0, q1, q2, q3, t):
        a, b, c = lerp(q0, q1, t), lerp(q1, q2, t), lerp(q2, q3, t)
        d, e = lerp(a, b, t), lerp(b, c, t)
        f = lerp(d, e, t)
        return (q0, a, d, f), (f, e, c, q3)

    _left, right = cut(p0, c1, c2, p1, t0)
    if t1 >= 1.0:
        return right
    s = (t1 - t0) / (1.0 - t0) if t0 < 1.0 else 0.0
    left, _right = cut(*right, s)
    return left


def fit_circle(pts) -> tuple:
    """Kasa least-squares circle: ``(cx, cy, r, rms)``; ``r`` is 0 when
    the points are collinear."""
    a = np.asarray(pts, dtype=float)
    if len(a) < 3:
        return (0.0, 0.0, 0.0, float("inf"))
    x, y = a[:, 0], a[:, 1]
    mx, my = x.mean(), y.mean()
    u, v = x - mx, y - my
    A = np.column_stack([u, v, np.ones_like(u)])
    b = u * u + v * v
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return (0.0, 0.0, 0.0, float("inf"))
    cu, cv = sol[0] / 2.0, sol[1] / 2.0
    r2 = sol[2] + cu * cu + cv * cv
    if r2 <= 0 or not np.isfinite(r2):
        return (0.0, 0.0, 0.0, float("inf"))
    r = math.sqrt(r2)
    rms = float(np.sqrt(np.mean((np.hypot(u - cu, v - cv) - r) ** 2)))
    return (float(cu + mx), float(cv + my), r, rms)


def arc_span_deg(pts, cx: float, cy: float) -> float:
    """Angular sweep covered by ordered points about a center, degrees."""
    if len(pts) < 2:
        return 0.0
    tot = 0.0
    prev = math.atan2(pts[0][1] - cy, pts[0][0] - cx)
    for p in pts[1:]:
        a = math.atan2(p[1] - cy, p[0] - cx)
        d = a - prev
        while d > math.pi:
            d -= 2 * math.pi
        while d < -math.pi:
            d += 2 * math.pi
        tot += d
        prev = a
    return abs(math.degrees(tot))


# --------------------------------------------------------------- raster --

def raster(polys, w: int, h: int, even_odd: bool = True) -> np.ndarray:
    """Scanline-fill polygons (pixel coordinates) into an ``h x w`` bool
    mask.  A pixel is set when its CENTER is inside.  ``even_odd=False``
    uses the nonzero winding rule."""
    w, h = int(w), int(h)
    acc = np.zeros((h, w + 1), dtype=np.int32)
    if w <= 0 or h <= 0:
        return np.zeros((max(h, 0), max(w, 0)), dtype=bool)
    for poly in polys:
        n = len(poly)
        if n < 3:
            continue
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            if y0 == y1:
                continue
            sgn = 1
            if y0 > y1:
                x0, y0, x1, y1 = x1, y1, x0, y0
                sgn = -1
            r0 = max(0, int(math.ceil(y0 - 0.5)))
            r1 = min(h - 1, int(math.ceil(y1 - 0.5)) - 1)
            if r1 < r0:
                continue
            rows = np.arange(r0, r1 + 1)
            xs = x0 + (rows + 0.5 - y0) * (x1 - x0) / (y1 - y0)
            cols = np.clip(np.ceil(xs - 0.5).astype(np.int64), 0, w)
            np.add.at(acc, (rows, cols), 1 if even_odd else sgn)
    cs = np.cumsum(acc, axis=1)[:, :w]
    return (cs % 2 == 1) if even_odd else (cs != 0)


def box_count(mask: np.ndarray, r: int) -> np.ndarray:
    """Count of set pixels in each ``(2r+1)^2`` window (outside = unset)."""
    m = np.asarray(mask, dtype=np.int32)
    h, w = m.shape
    ii = np.zeros((h + 1, w + 1), dtype=np.int64)
    ii[1:, 1:] = m.cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(h) - r, 0, h)
    y1 = np.clip(np.arange(h) + r + 1, 0, h)
    x0 = np.clip(np.arange(w) - r, 0, w)
    x1 = np.clip(np.arange(w) + r + 1, 0, w)
    return (ii[y1][:, x1] - ii[y0][:, x1] - ii[y1][:, x0] + ii[y0][:, x0])


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return np.asarray(mask, dtype=bool).copy()
    return box_count(mask, r) > 0


def erode(mask: np.ndarray, r: int) -> np.ndarray:
    """Square-element erosion; pixels outside the array count as unset."""
    if r <= 0:
        return np.asarray(mask, dtype=bool).copy()
    k = 2 * r + 1
    return box_count(mask, r) == k * k


def opening(mask: np.ndarray, r: int) -> np.ndarray:
    return dilate(erode(mask, r), r) & np.asarray(mask, dtype=bool)


def closing(mask: np.ndarray, r: int) -> np.ndarray:
    """Dilate then erode (padded so the array edge does not eat the
    result)."""
    if r <= 0:
        return np.asarray(mask, dtype=bool).copy()
    m = np.pad(np.asarray(mask, dtype=bool), r + 1)
    out = erode(dilate(m, r), r)
    return out[r + 1:-(r + 1), r + 1:-(r + 1)]


def min_filter(a: np.ndarray, r: int) -> np.ndarray:
    """Sliding minimum over a ``(2r+1)^2`` window (edge-replicated)."""
    out = np.asarray(a)
    if r <= 0:
        return out.copy()
    for axis in (0, 1):
        out = _run_min(out, 2 * r + 1, axis)
    return out


def _run_min(a: np.ndarray, k: int, axis: int) -> np.ndarray:
    a = np.moveaxis(a, axis, 0)
    r = k // 2
    pad = [(r, r)] + [(0, 0)] * (a.ndim - 1)
    p = np.pad(a, pad, mode="edge")
    # doubling: m[j] = min over p[j : j + span]
    span = 1
    m = p
    while span * 2 <= k:
        m = np.minimum(m[:-span], m[span:])
        span *= 2
    n = a.shape[0]
    rest = k - span
    out = np.minimum(m[:n], m[rest:rest + n]) if rest else m[:n]
    return np.moveaxis(out, 0, axis)


def label(mask: np.ndarray):
    """8-connected components -> ``(labels int32, [box, ...])`` where each
    box has ``label, x0, y0, x1, y1, area`` (the Tracer's labeller)."""
    from .tracer.components import label as _label
    return _label(np.asarray(mask, dtype=bool))


# -------------------------------------------------------------- tracing --

def trace(mask: np.ndarray, holes: bool = False) -> list:
    """Boundary loops of a bool mask in pixel-CORNER coordinates.

    Every set pixel contributes its sides that border an unset pixel; the
    sides chain into closed loops (4-connected foreground: pixels touching
    only at a corner trace as separate loops).  Returns the OUTER loops as
    lists of ``(x, y)`` corner points, collinear runs merged; with
    ``holes=True`` returns ``[(loop, is_hole), ...]``."""
    m = np.asarray(mask, dtype=bool)
    h, w = m.shape
    if h == 0 or w == 0 or not m.any():
        return []
    p = np.pad(m, 1)
    core = p[1:-1, 1:-1]
    nxt: dict = {}

    def add(sx, sy, ex, ey):
        for a, b, c, d in zip(sx.tolist(), sy.tolist(), ex.tolist(), ey.tolist()):
            nxt.setdefault((a, b), []).append((c, d))

    r, c = np.nonzero(core & ~p[:-2, 1:-1])          # top side: go right
    add(c, r, c + 1, r)
    r, c = np.nonzero(core & ~p[1:-1, 2:])           # right side: go down
    add(c + 1, r, c + 1, r + 1)
    r, c = np.nonzero(core & ~p[2:, 1:-1])           # bottom side: go left
    add(c + 1, r + 1, c, r + 1)
    r, c = np.nonzero(core & ~p[1:-1, :-2])          # left side: go up
    add(c, r + 1, c, r)

    loops = []
    while nxt:
        start = next(iter(nxt))
        cur = start
        prev_dir = None
        loop = []
        while True:
            outs = nxt.get(cur)
            if not outs:
                break
            if len(outs) == 1 or prev_dir is None:
                nx = outs.pop(0)
            else:
                # pinch vertex: take the sharpest right turn so diagonal
                # neighbours stay separate loops
                def turn(q):
                    dx, dy = q[0] - cur[0], q[1] - cur[1]
                    return prev_dir[0] * dy - prev_dir[1] * dx
                nx = max(outs, key=turn)
                outs.remove(nx)
            if not outs:
                del nxt[cur]
            d = (nx[0] - cur[0], nx[1] - cur[1])
            if prev_dir is None or d != prev_dir:
                loop.append((float(cur[0]), float(cur[1])))
            prev_dir = d
            cur = nx
            if cur == start:
                break
        if len(loop) >= 3:
            # the start vertex may sit mid-run: drop it when collinear
            a, b, c2 = loop[-1], loop[0], loop[1]
            if abs((b[0] - a[0]) * (c2[1] - b[1])
                   - (b[1] - a[1]) * (c2[0] - b[0])) < 1e-9:
                loop.pop(0)
            loops.append(loop)
    # with this side orientation an outer loop has positive shoelace area
    # in pixel (y-down) coordinates
    if holes:
        return [(lp, area(lp) < 0) for lp in loops]
    return [lp for lp in loops if area(lp) > 0]


def simplify(poly, eps: float) -> list:
    """Closed Douglas-Peucker: vertices farther than ``eps`` from the
    simplified outline are kept."""
    pts = dedupe(poly)
    n = len(pts)
    if n <= 3:
        return pts
    a = np.asarray(pts, dtype=float)
    # anchor on the two mutually farthest vertices (approximate: farthest
    # from the centroid, then farthest from that)
    c = a.mean(axis=0)
    i0 = int(np.argmax(((a - c) ** 2).sum(axis=1)))
    i1 = int(np.argmax(((a - a[i0]) ** 2).sum(axis=1)))
    if i0 > i1:
        i0, i1 = i1, i0
    keep = np.zeros(n, dtype=bool)
    keep[i0] = keep[i1] = True

    def rdp(idx):
        stack = [(0, len(idx) - 1)]
        while stack:
            s, e = stack.pop()
            if e - s < 2:
                continue
            p0, p1 = a[idx[s]], a[idx[e]]
            seg = p1 - p0
            ln = math.hypot(seg[0], seg[1])
            mid = a[idx[s + 1:e]]
            if ln < 1e-12:
                d = np.hypot(mid[:, 0] - p0[0], mid[:, 1] - p0[1])
            else:
                d = np.abs(seg[0] * (mid[:, 1] - p0[1])
                           - seg[1] * (mid[:, 0] - p0[0])) / ln
            k = int(np.argmax(d))
            if d[k] > eps:
                keep[idx[s + 1 + k]] = True
                stack.append((s, s + 1 + k))
                stack.append((s + 1 + k, e))

    rdp(list(range(i0, i1 + 1)))
    rdp(list(range(i1, n)) + list(range(0, i0 + 1)))
    return [pts[i] for i in range(n) if keep[i]]


def refit(loop, eps: float = 1.5, trim: float = 2.0, stub: float = 4.5) -> list:
    """Sub-pixel polygon from a traced pixel loop.

    Douglas-Peucker fixes the vertex structure; each edge is then refitted
    as a least-squares line through the MIDPOINTS of the loop's stair
    corners along it (a traced staircase alternates between the two sides
    of the true edge; points within ``trim`` of an end are left out so the
    step at a corner does not bias the fit).  Vertices are re-placed at
    the intersections of consecutive fitted lines.  A run of edges shorter
    than ``stub`` between two long edges is the stair-step AT a corner,
    not a real face: it collapses into the single intersection of its two
    long neighbours when that intersection lands beside it."""
    pts = dedupe(loop)
    simp = simplify(pts, eps)
    k = len(simp)
    if k < 3:
        return simp
    idx = []
    pos = 0
    n = len(pts)
    for v in simp:
        while pts[pos % n] != v:
            pos += 1
        idx.append(pos % n)
        pos += 1
    a = np.asarray(pts, dtype=float)
    lines, lens = [], []
    for i in range(k):
        s, e = idx[i], idx[(i + 1) % k]
        run = (list(range(s, e + 1)) if e > s
               else list(range(s, n)) + list(range(0, e + 1)))
        p0, p1 = a[run[0]], a[run[-1]]
        seg = p1 - p0
        ln = math.hypot(seg[0], seg[1])
        lens.append(ln)
        q = a[run]
        if ln > 2 * trim + 2:
            t = ((q[:, 0] - p0[0]) * seg[0] + (q[:, 1] - p0[1]) * seg[1]) / ln
            q = q[(t >= trim) & (t <= ln - trim)]
        if len(q) >= 4 and ln > 1e-9:
            mid = (q[:-1] + q[1:]) / 2.0
            c = mid.mean(axis=0)
            _u, _s, vt = np.linalg.svd(mid - c)
            d = vt[0]
            lines.append(((c[0], c[1]), (c[0] + d[0], c[1] + d[1])))
        else:
            lines.append(((p0[0], p0[1]), (p1[0], p1[1])))
    long_ix = [i for i in range(k) if lens[i] >= stub]
    if len(long_ix) < 3:
        long_ix = list(range(k))
    out = []
    m = len(long_ix)
    for j in range(m):
        ia, ib = long_ix[j - 1], long_ix[j]
        # vertices between long edge ia and long edge ib (cyclic)
        between = []
        v = (ia + 1) % k
        while True:
            between.append(simp[v])
            if v == ib:
                break
            v = (v + 1) % k
        x = line_intersection(lines[ia][0], lines[ia][1],
                              lines[ib][0], lines[ib][1])
        cx = sum(p[0] for p in between) / len(between)
        cy = sum(p[1] for p in between) / len(between)
        reach = stub * len(between) + 2.0 * max(eps, 1.0)
        if x is not None and math.hypot(x[0] - cx, x[1] - cy) <= reach:
            out.append((float(x[0]), float(x[1])))
        else:
            out.extend((float(p[0]), float(p[1])) for p in between)
    return out


# ------------------------------------------------------ polygon unions --

def union_corners(polys, r: float, tol: float | None = None) -> list:
    """Corner points of the UNION of polygons (the true face-of-concrete
    corners when walls are drawn as overlapping or abutting pieces).

    Candidates are every polygon vertex plus every edge-edge crossing;
    a candidate is a corner when the union's boundary actually turns
    there.  That is measured, not inferred: 32 probe points on a circle of
    radius ``r`` around the candidate are tested for containment in any
    polygon, and the candidate is a corner unless the inside set is empty,
    full, or one contiguous arc of about a half turn (a straight face).

    Returns ``[{"pt": (x, y), "inside_deg": float, "polys": [indices]}]``;
    ``inside_deg`` is about 90 for a convex corner and about 270 for a
    re-entrant one."""
    tol = r * 0.25 if tol is None else tol
    cands: list = []
    for i, p in enumerate(polys):
        for v in p:
            cands.append(((float(v[0]), float(v[1])), i))
    boxes = [bbox(p) for p in polys]
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            a, b = boxes[i], boxes[j]
            if a[2] < b[0] - tol or b[2] < a[0] - tol or a[3] < b[1] - tol or b[3] < a[1] - tol:
                continue
            pi, pj = polys[i], polys[j]
            for s in range(len(pi)):
                for t in range(len(pj)):
                    x = seg_intersection(pi[s], pi[(s + 1) % len(pi)],
                                         pj[t], pj[(t + 1) % len(pj)])
                    if x is not None:
                        cands.append(((x[0], x[1]), i))
    out: list = []
    n_probe = 32
    # half-step phase: no probe lands exactly on an axis-aligned face
    ang = [2 * math.pi * (k + 0.5) / n_probe for k in range(n_probe)]
    for (x, y), src in cands:
        if any(math.hypot(x - o["pt"][0], y - o["pt"][1]) <= tol for o in out):
            continue
        near = [k for k, b in enumerate(boxes)
                if b[0] - r <= x <= b[2] + r and b[1] - r <= y <= b[3] + r]
        ins = []
        for a in ang:
            px, py = x + r * math.cos(a), y + r * math.sin(a)
            ins.append(any(point_in_poly(px, py, polys[k]) for k in near))
        cnt = sum(ins)
        if cnt == 0 or cnt == n_probe:
            continue
        runs = sum(1 for k in range(n_probe) if ins[k] and not ins[k - 1])
        deg = 360.0 * cnt / n_probe
        if runs == 1 and abs(deg - 180.0) <= 360.0 / n_probe * 1.5:
            continue                                  # a straight face
        out.append({"pt": (x, y), "inside_deg": deg, "polys": near})
    return out
