"""Grid reading: gridlines, bubbles, names, offsets, and the frame.

Brief section 5.2: find gridlines and bubbles, build the grid with names
and offsets, check the spacing against the printed grid dimensions and,
when a CAD background exists, against it.  A mismatch over 1/4 in. blocks
the level.

A gridline is a long visible line that runs into a bubble: a stroked
circle holding a short name.  Dash-dot gridlines that the CAD export
exploded into separate dashes are merged back into lines first.  Two
perpendicular families are kept; a named line skewed to both is reported,
never forced into a family.

The grid also carries the FRAME.  The local grid frame puts the origin at
the first x-line / first y-line crossing with grid north up; a CAD or
control frame is a least-squares similarity fitted through the named grid
crossings.  Viewer points in, world feet out.
"""
from __future__ import annotations

import math
import re

import numpy as np

from .. import geom2d as g2
from . import model as M

NAME_RE = re.compile(r"^[A-Z0-9]{1,3}(?:\.[0-9]{1,2})?[A-Z']?$")
MIN_LINE_PT = 60.0
PERP_DEG = 1.0
DIM_END_PT = 1.5


# ------------------------------------------------------------ primitives --

def merge_collinear(segs, ang_tol_deg: float = 0.3, rho_tol: float = 0.6,
                    gap: float = 14.0) -> list:
    """Merge collinear segments (exploded dash patterns) into lines.

    ``segs`` are ``(a, b, length)``.  Returns ``[{"a", "b", "length",
    "cover"}]`` where ``cover`` is the inked share of the merged length."""
    buckets: dict = {}
    for a, b, ln in segs:
        if ln <= 0:
            continue
        th = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
        if th > math.pi - math.radians(ang_tol_deg) / 2:
            th -= math.pi
        ux, uy = math.cos(th), math.sin(th)
        rho = -a[0] * uy + a[1] * ux
        key = (round(math.degrees(th) / ang_tol_deg), round(rho / rho_tol))
        buckets.setdefault(key, []).append((a, b, ln, th, rho))
    # neighbouring buckets belong together (a line straddling a bin edge)
    out = []
    seen = set()
    keys = sorted(buckets)
    for key in keys:
        if key in seen:
            continue
        group = []
        stack = [key]
        while stack:
            k = stack.pop()
            if k in seen or k not in buckets:
                continue
            seen.add(k)
            group.extend(buckets[k])
            for da in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    nk = (k[0] + da, k[1] + dr)
                    if nk not in seen and nk in buckets:
                        stack.append(nk)
        th = sum(s[3] * s[2] for s in group) / sum(s[2] for s in group)
        ux, uy = math.cos(th), math.sin(th)
        rho = sum(s[4] * s[2] for s in group) / sum(s[2] for s in group)
        iv = sorted((min(s[0][0] * ux + s[0][1] * uy, s[1][0] * ux + s[1][1] * uy),
                     max(s[0][0] * ux + s[0][1] * uy, s[1][0] * ux + s[1][1] * uy))
                    for s in group)
        runs = []
        for lo, hi in iv:
            if runs and lo - runs[-1][1] <= gap:
                runs[-1][1] = max(runs[-1][1], hi)
                runs[-1][2] += hi - lo
            else:
                runs.append([lo, hi, hi - lo])
        for lo, hi, ink in runs:
            a = (lo * ux - rho * uy, lo * uy + rho * ux)
            b = (hi * ux - rho * uy, hi * uy + rho * ux)
            ln = hi - lo
            if ln > 0:
                out.append({"a": a, "b": b, "length": ln, "cover": min(1.0, ink / ln)})
    return out


def find_circles(strokes, r_min: float = 3.0, r_max: float = 40.0) -> list:
    """Stroked full circles -> ``[{"center", "r", "seq"}]``."""
    by_seq: dict = {}
    for st in strokes:
        by_seq.setdefault(st.seq, []).append(st)
    out = []
    for seq, sts in by_seq.items():
        if len(sts) < 2 or any(st.curve is None for st in sts):
            continue
        pts = [p for st in sts for p in st.pts]
        cx, cy, r, rms = g2.fit_circle(pts)
        if not (r_min <= r <= r_max) or rms > max(0.03 * r, 0.15):
            continue
        ang = sorted(math.atan2(p[1] - cy, p[0] - cx) for p in pts)
        gaps = [b - a for a, b in zip(ang, ang[1:])] + [ang[0] + 2 * math.pi - ang[-1]]
        if max(gaps) > math.radians(40):
            continue                                   # an arc, not a full circle
        out.append({"center": (cx, cy), "r": r, "seq": seq})
    return out


def find_bubbles(strokes, words) -> list:
    """Circles that hold a short name -> ``[{"center", "r", "name"}]``."""
    out = []
    for c in find_circles(strokes):
        cx, cy = c["center"]
        inside = [w for w in words
                  if math.hypot((w[0] + w[2]) / 2 - cx, (w[1] + w[3]) / 2 - cy) <= 0.9 * c["r"]]
        if not inside:
            continue
        inside.sort(key=lambda w: (round(w[1] / 4.0), w[0]))
        name = "".join(w[4].strip() for w in inside).upper()
        if NAME_RE.match(name):
            out.append({"center": (cx, cy), "r": c["r"], "name": name})
    return out


def fit_similarity(src, dst) -> dict:
    """Least-squares similarity WITH the page flip: maps y-down page points
    ``src`` onto y-up world points ``dst``.

    ``e = p*x + q*y + tx``, ``n = q*x - p*y + ty``.  Returns the Frame
    coefficients plus ``scale`` (world units per source unit) and the
    residuals (world units)."""
    s = np.asarray(src, dtype=float)
    d = np.asarray(dst, dtype=float)
    n = len(s)
    A = np.zeros((2 * n, 4))
    b = np.zeros(2 * n)
    A[0::2, 0], A[0::2, 1], A[0::2, 2] = s[:, 0], s[:, 1], 1.0
    A[1::2, 0], A[1::2, 1], A[1::2, 3] = -s[:, 1], s[:, 0], 1.0
    b[0::2], b[1::2] = d[:, 0], d[:, 1]
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    p, q, tx, ty = (float(v) for v in sol)
    pred_e = p * s[:, 0] + q * s[:, 1] + tx
    pred_n = q * s[:, 0] - p * s[:, 1] + ty
    res = np.hypot(pred_e - d[:, 0], pred_n - d[:, 1])
    return {"a": p, "b": q, "c": q, "d": -p, "tx": tx, "ty": ty,
            "scale": math.hypot(p, q), "rms": float(np.sqrt((res ** 2).mean())),
            "max": float(res.max()), "residuals": res.tolist()}


def fit_rigid(src, dst) -> dict:
    """Least-squares similarity WITHOUT a flip (both frames y-up): maps
    one plan frame onto another.  ``e = p*x - q*y + tx``,
    ``n = q*x + p*y + ty``.  Returns ``{"fn", "scale", "rms", "max"}``;
    ``fn(x, y)`` applies it."""
    s = np.asarray(src, dtype=float)
    d = np.asarray(dst, dtype=float)
    n = len(s)
    A = np.zeros((2 * n, 4))
    b = np.zeros(2 * n)
    A[0::2, 0], A[0::2, 1], A[0::2, 2] = s[:, 0], -s[:, 1], 1.0
    A[1::2, 0], A[1::2, 1], A[1::2, 3] = s[:, 1], s[:, 0], 1.0
    b[0::2], b[1::2] = d[:, 0], d[:, 1]
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    p, q, tx, ty = (float(v) for v in sol)
    res = np.hypot(p * s[:, 0] - q * s[:, 1] + tx - d[:, 0],
                   q * s[:, 0] + p * s[:, 1] + ty - d[:, 1])

    def fn(x, y):
        return (p * x - q * y + tx, q * x + p * y + ty)

    return {"fn": fn, "scale": math.hypot(p, q), "rms": float(np.sqrt((res ** 2).mean())),
            "max": float(res.max())}


# ------------------------------------------------------------- the grid --

def _families(lines, y_up: bool) -> tuple:
    """Split named lines into the two perpendicular families.
    Returns ``(x_lines, y_lines, skewed, u_e)``."""
    if not lines:
        return [], [], [], (1.0, 0.0)

    def ang(ln):
        return math.degrees(math.atan2(ln["b"][1] - ln["a"][1],
                                       ln["b"][0] - ln["a"][0])) % 180.0

    def near(a, b):
        d = abs(a - b) % 180.0
        return min(d, 180.0 - d)

    angs = [ang(ln) for ln in lines]
    # the modal direction, by total length
    best = max(angs, key=lambda a: sum(ln["length"] for ln, x in zip(lines, angs)
                                       if near(x, a) <= PERP_DEG))
    fam_a = [ln for ln, x in zip(lines, angs) if near(x, best) <= PERP_DEG]
    fam_b = [ln for ln, x in zip(lines, angs) if near(x, best + 90.0) <= PERP_DEG]
    skew = [ln for ln, x in zip(lines, angs)
            if near(x, best) > PERP_DEG and near(x, best + 90.0) > PERP_DEG]
    # the x family runs along grid north: the more vertical one on the sheet
    if abs(math.sin(math.radians(best))) >= abs(math.cos(math.radians(best))):
        xs, ys, th_y = fam_a, fam_b, best + 90.0
    else:
        xs, ys, th_y = fam_b, fam_a, best
    ux, uy = math.cos(math.radians(th_y)), math.sin(math.radians(th_y))
    if ux < 0:
        ux, uy = -ux, -uy
    return xs, ys, skew, (ux, uy)


def build_grid(named_lines, unit_per_ft: float, y_up: bool) -> tuple:
    """Named lines ``[{"name", "a", "b", "length", "bubble"}]`` in some
    planar coordinates -> ``(Grid, problems, axes)``.  ``axes`` holds
    ``u_e``, ``u_n`` and the raw origin offsets ``e0`` / ``n0`` (the
    numbers the local frame is made of)."""
    problems = []
    xs, ys, skew, u_e = _families(named_lines, y_up)
    u_n = (-u_e[1], u_e[0]) if y_up else (u_e[1], -u_e[0])
    for ln in skew:
        problems.append(f"gridline {ln['name']} is skewed to the grid and was not read")
    grid = M.Grid()
    seen = {}
    rows = []
    for fam, axis, u in ((xs, "x", u_e), (ys, "y", u_n)):
        for ln in fam:
            mx = (ln["a"][0] + ln["b"][0]) / 2.0
            my = (ln["a"][1] + ln["b"][1]) / 2.0
            rows.append((axis, mx * u[0] + my * u[1], ln))
    e0 = min((r[1] for r in rows if r[0] == "x"), default=0.0)
    n0 = min((r[1] for r in rows if r[0] == "y"), default=0.0)
    for axis, raw, ln in rows:
        key = ln["name"]
        if key in seen:
            problems.append(f"two gridlines are named {key}; the second was not read")
            continue
        seen[key] = True
        grid.lines.append(M.GridLine(
            name=key, axis=axis,
            offset_ft=(raw - (e0 if axis == "x" else n0)) / unit_per_ft,
            page_a=tuple(ln["a"]), page_b=tuple(ln["b"]), bubble=ln.get("bubble")))
    if len(grid.family("x")) < 2 or len(grid.family("y")) < 2:
        problems.append(
            f"grid not read: {len(grid.family('x'))} line(s) one way and "
            f"{len(grid.family('y'))} the other (need two each way)")
    return grid, problems, {"u_e": u_e, "u_n": u_n, "e0": e0, "n0": n0}


def read_grid(strokes, words, ppf: float) -> tuple:
    """Read the grid off a plan page -> ``(Grid, problems, axes)``."""
    from .dimread import line_segments
    bubbles = find_bubbles(strokes, words)
    merged = [m for m in merge_collinear(line_segments(strokes, 2.0))
              if m["length"] >= MIN_LINE_PT and m["cover"] >= 0.35]
    named = []
    for bub in bubbles:
        cx, cy = bub["center"]
        best = None
        for m in merged:
            a, b = m["a"], m["b"]
            ln = m["length"]
            ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
            perp = abs(-(cx - a[0]) * uy + (cy - a[1]) * ux)
            if perp > max(0.5 * bub["r"], 1.5):
                continue
            t = (cx - a[0]) * ux + (cy - a[1]) * uy
            end_gap = -t if t < 0 else (t - ln if t > ln else 0.0)
            if t < -(3.0 * bub["r"] + 12.0) or t > ln + 3.0 * bub["r"] + 12.0:
                continue
            if 0.15 * ln < t < 0.85 * ln:
                continue                               # the circle sits ON a line, not at its end
            if best is None or m["length"] > best[0]["length"]:
                best = (m, end_gap)
        if best is not None:
            named.append({"name": bub["name"], "a": best[0]["a"], "b": best[0]["b"],
                          "length": best[0]["length"], "bubble": bub["center"]})
    grid, problems, axes = build_grid(named, ppf, y_up=False)
    if not bubbles:
        problems.insert(0, "no grid bubbles were found on the sheet")
    return grid, problems, axes


def local_frame(grid: M.Grid, axes: dict, ppf: float) -> M.Frame:
    """The grid-based local frame: origin at the first x-line / first
    y-line crossing, grid north up."""
    u_e, u_n = axes["u_e"], axes["u_n"]
    gx = grid.family("x")[0].name if grid.family("x") else "?"
    gy = grid.family("y")[0].name if grid.family("y") else "?"
    return M.Frame(a=u_e[0] / ppf, b=u_e[1] / ppf, c=u_n[0] / ppf, d=u_n[1] / ppf,
                   tx=-axes["e0"] / ppf, ty=-axes["n0"] / ppf, kind="grid",
                   label=(f"Local grid frame: origin at grid {gx}/{gy}, grid north up. "
                          "Not real-world coordinates."))


def place_in_world(grid: M.Grid, frame: M.Frame) -> None:
    """Fill every line's world ends from its page ends."""
    for g in grid.lines:
        g.world_a = frame.to_world(*g.page_a)
        g.world_b = frame.to_world(*g.page_b)


def spacing_checks(grid: M.Grid, dims, axes: dict) -> list:
    """Printed grid dimensions against the drawn spacing.

    A dimension string whose two ends each land on a gridline of one
    family (and that runs square to that family) is a printed grid
    dimension.  Returns ``[{"axis", "a", "b", "printed_ft", "measured_ft",
    "delta_ft", "text"}]``."""
    out = []
    for d in dims:
        a, b = d["a"], d["b"]
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln <= 0:
            continue
        ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
        for axis, u in (("x", axes["u_e"]), ("y", axes["u_n"])):
            if abs(ux * u[0] + uy * u[1]) < math.cos(math.radians(2.0)):
                continue
            ends = []
            for p in (a, b):
                hit = None
                for g in grid.family(axis):
                    if g2.dist_point_line(p, g.page_a, g.page_b) <= DIM_END_PT:
                        hit = g
                        break
                ends.append(hit)
            if ends[0] is None or ends[1] is None or ends[0] is ends[1]:
                continue
            measured = abs(ends[1].offset_ft - ends[0].offset_ft)
            lo, hi = sorted(ends, key=lambda g: g.offset_ft)
            out.append({"axis": axis, "a": lo.name, "b": hi.name,
                        "printed_ft": d["value_ft"], "measured_ft": measured,
                        "delta_ft": measured - d["value_ft"], "text": d["text"]})
    return out


def compare_grids(a: M.Grid, b: M.Grid) -> list:
    """Same-named spacing in two grids (the sheet's against the CAD
    background's, or the architect's against the structure's).  Returns
    ``[{"axis", "a", "b", "first_ft", "second_ft", "delta_ft"}]`` for every
    consecutive pair of lines both grids carry, plus ``{"missing": name}``
    rows for names only one of them has."""
    out = []
    for axis in ("x", "y"):
        fa = {g.name: g for g in a.family(axis)}
        fb = {g.name: g for g in b.family(axis)}
        common = [g.name for g in a.family(axis) if g.name in fb]
        for n1, n2 in zip(common, common[1:]):
            s1 = abs(fa[n2].offset_ft - fa[n1].offset_ft)
            s2 = abs(fb[n2].offset_ft - fb[n1].offset_ft)
            out.append({"axis": axis, "a": n1, "b": n2, "first_ft": s1,
                        "second_ft": s2, "delta_ft": s1 - s2})
        for name in sorted(set(fa) ^ set(fb)):
            out.append({"missing": name, "axis": axis,
                        "in": "first" if name in fa else "second"})
    return out


def frame_from_pairs(grid: M.Grid, targets: dict, kind: str, label: str,
                     ppf: float) -> tuple:
    """Fit a frame through named grid crossings.

    ``targets`` maps crossing names (``"A/1"``) to world ``(e, n)`` feet.
    Returns ``(Frame | None, info)``; ``info`` carries ``n``, ``rms_ft``,
    ``max_ft``, ``scale_ratio`` (fitted scale against the sheet scale; 1.0
    means they agree) and ``problem`` when no frame could be fitted."""
    page = grid.page_intersections()
    names = [k for k in targets if k in page]
    if len(names) < 2:
        return None, {"n": len(names), "problem":
                      f"only {len(names)} named grid crossing(s) in common; two are needed"}
    fit = fit_similarity([page[k] for k in names], [targets[k] for k in names])
    fr = M.Frame(a=fit["a"], b=fit["b"], c=fit["c"], d=fit["d"], tx=fit["tx"],
                 ty=fit["ty"], kind=kind, label=label)
    return fr, {"n": len(names), "rms_ft": fit["rms"], "max_ft": fit["max"],
                "scale_ratio": fit["scale"] * ppf, "names": names}
