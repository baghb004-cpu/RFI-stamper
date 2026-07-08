"""Harvest: model-to-points generators — geometry in, stakeable point
PROPOSALS out (brief section 4).

Every generator is PURE: it returns a list of proposal dicts and touches
nothing — no job, no sidecar, no numbering.  The GUI shows the ghost-pin
preview ("will create 412 points"), the human commits, and only then do
proposals become real points through ``LayoutJob.add_point`` /
``add_witness``.  Nothing exists until committed.

Proposal dict shape (the contract the Harvest drawer builds against)::

    {"n": float, "e": float,        # world feet (Northing / Easting), OR
     "x": float, "y": float,        # page points when frame="page"
     "elev": float | None,          # never conflate None with 0.0
     "z_ref": "FF",                 # FF | TOS | deck-above | datum
     "name": str | None,            # suggested label (grid cell, child id)
     "desc": str, "code": str, "layer": str,
     "provenance": {"gen", "key", "rule", "params"},
     "witness": {"offset_ft", "azimuth"}}   # only when requested

``provenance`` is the re-harvest identity: ``(gen, key)`` must be stable
across re-runs of the same generator on changed geometry — that is what
:func:`reharvest_diff` matches on.

Geometry input convention: all point arguments are ``(x, y)`` pairs in the
shared plan frame where **x = Easting and y = Northing** (decimal feet,
y up = plan north) — exactly what :meth:`draft.DraftModel.wall_segments`,
:func:`draft.grid_points` and :func:`extrude.to_world` produce.  Pass
``frame="page"`` where supported to emit page-point proposals instead.

Fully offline; stdlib only.
"""
from __future__ import annotations

import math

#: Bucket threshold for :func:`reharvest_diff` — 1/16 in in decimal feet.
DIFF_TOL_FT = 0.005

#: Printed on every bolt-cage field card (brief section 4.5): emitting four
#: independent bolt points encourages the wrong field method.
BOLT_GROUP_NOTE = (
    "Intra-group 1/8 in is held by a template jig on the group work point "
    "— stake the work point + rotation, set the jig, then the rods. "
    "Group tolerances: 1/8 in between any two rods in a group; 1/4 in "
    "between adjacent group centers; 1/4 in group-to-column-line; rod tops "
    "+/-1/2 in.")


# ------------------------------------------------------------ stride rules --

#: Named per-trade stride defaults (brief section 4.3) — a size->spacing
#: lookup per material, NEVER one global interval.  All editable, all
#: labeled with their basis; rows marked ``verified=False`` are engineering
#: spans, not code — verify against project spec.  ``sizes`` rows are
#: ``(max_size_in, stride_ft)`` ladders consumed by :func:`stride_for`.
STRIDE_RULES = {
    "STEEL-THREADED": {
        "stride_ft": 12.0, "v_stride_ft": 15.0, "verified": True,
        "basis": "threaded steel pipe practice: 12 ft horizontal / "
                 "15 ft vertical"},
    "COPPER": {
        "sizes": ((1.25, 6.0), (None, 10.0)), "v_stride_ft": 10.0,
        "verified": True,
        "basis": "copper <= 1-1/4 in: 6 ft; >= 1-1/2 in: 10 ft "
                 "(10 ft vertical)"},
    "CPVC": {
        "sizes": ((1.0, 3.0), (None, 4.0)), "verified": True,
        "basis": "CPVC <= 1 in: 3 ft; >= 1-1/4 in: 4 ft"},
    "PVC": {
        "stride_ft": 4.0, "verified": True, "basis": "PVC: 4 ft"},
    "CAST-IRON": {
        "stride_ft": 5.0, "verified": True,
        "basis": "cast iron: 5 ft (10 ft with 10-ft pipe lengths)"},
    "STEEL-BY-SIZE": {
        "sizes": ((1.0, 7.0), (1.5, 9.0), (2.0, 10.0), (3.0, 12.0),
                  (4.0, 14.0), (6.0, 17.0), (None, 19.0)),
        "verified": False,
        "basis": "size-based engineering spans (UNVERIFIED — verify "
                 "against project spec): 1/2-1 in 7 ft, 1-1/2 9, 2 10, "
                 "3 12, 4 14, 6 17, 8 19 ft"},
    "DUCT": {
        "stride_ft": 9.0, "wide_stride_ft": 4.5, "wide_over_in": 60.0,
        "verified": True,
        "basis": "duct: 8-10 ft typical; 4-5 ft past ~60 in width; "
                 "support within 2 ft of duct-mounted equipment"},
}


def stride_for(rule: str, size_in: float | None = None) -> float:
    """Stride (ft) for a named rule, size-banded where the rule carries a
    ``sizes`` ladder — the first ``(max_size_in, stride)`` row whose cap
    covers ``size_in`` wins (``None`` cap = everything larger)."""
    try:
        r = STRIDE_RULES[str(rule).upper()]
    except KeyError:
        raise ValueError(f"unknown stride rule {rule!r}; expected one of "
                         f"{sorted(STRIDE_RULES)}") from None
    sizes = r.get("sizes")
    if sizes:
        if size_in is None:
            raise ValueError(f"stride rule {rule!r} is size-banded — pass "
                             "size_in (spacing is a size->spacing lookup "
                             "per material, never one global interval)")
        for cap, stride in sizes:
            if cap is None or float(size_in) <= cap:
                return float(stride)
    return float(r["stride_ft"])


# --------------------------------------------------------------- internals --

def _prop(gen: str, key, *, e=None, n=None, x=None, y=None, elev=None,
          z_ref="FF", name=None, desc="", code="", layer="",
          rule="", params=None) -> dict:
    out = {
        "elev": elev, "z_ref": z_ref, "name": name, "desc": desc,
        "code": code, "layer": layer,
        "provenance": {"gen": gen, "key": str(key), "rule": rule,
                       "params": dict(params or {})},
    }
    if x is not None:
        out["x"], out["y"] = float(x), float(y)
    else:
        out["n"], out["e"] = float(n), float(e)
    return out


def _seg_x(a1, a2, b1, b2, extend=False):
    """Segment-segment (or, with ``extend``, line-line) intersection point;
    parallel/collinear pairs return None (an overlap has no single crossing
    worth staking)."""
    rx, ry = a2[0] - a1[0], a2[1] - a1[1]
    sx, sy = b2[0] - b1[0], b2[1] - b1[1]
    den = rx * sy - ry * sx
    scale = math.hypot(rx, ry) * math.hypot(sx, sy)
    if abs(den) <= 1e-12 + 1e-9 * scale:
        return None
    qx, qy = b1[0] - a1[0], b1[1] - a1[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    if not extend and not (-1e-9 <= t <= 1.0 + 1e-9
                           and -1e-9 <= u <= 1.0 + 1e-9):
        return None
    return (a1[0] + rx * t, a1[1] + ry * t)


def _label_order(label: str):
    """Alpha labels sort before numeric ('A' + '1' composes as 'A1')."""
    s = str(label)
    return (0, s) if s[:1].isalpha() else (1, s.zfill(9))


def _cell_name(la: str, lb: str) -> str:
    a, b = sorted((str(la), str(lb)), key=_label_order)
    return f"{a}{b}"


# ----------------------------------------------------------------- gridiron --

def gridiron(source, lines_b=None, *, frame="world", layer="GRIDLINE",
             code="GL", elev=None, z_ref="FF") -> list:
    """Grid intersections ("Gridiron", brief section 4.1).

    ``source`` is either a :class:`draft.DraftModel` (its grid entities are
    crossed via the Loft bridge — Loft model space IS the world frame) or a
    list of labeled lines ``[(label, (x1, y1), (x2, y2)), ...]`` with
    ``lines_b`` as the second run.  Names concatenate row+col with the
    alpha label first (``A1``, ``C7``) — the universal work-point
    convention.  ``frame="page"`` (explicit lines only) emits page-point
    proposals."""
    props = []
    if hasattr(source, "ents"):                     # draft.DraftModel
        from . import draft
        for x, y, label in draft.grid_points(source):
            name = str(label).replace("/", "")
            props.append(_prop(
                "gridiron", name, e=x, n=y, elev=elev, z_ref=z_ref,
                name=name, desc=f"{code}-{name}", code=code, layer=layer,
                rule="grid intersections (Loft grids)",
                params={"source": "draft"}))
        return props
    if lines_b is None:
        raise ValueError("explicit gridiron needs two line runs: "
                         "gridiron(lines_a, lines_b)")
    if frame not in ("world", "page"):
        raise ValueError(f"unknown frame {frame!r}; expected world | page")
    for la, a1, a2 in source:
        for lb, b1, b2 in lines_b:
            p = _seg_x(a1, a2, b1, b2)
            if p is None:
                continue
            name = _cell_name(la, lb)
            kw = ({"x": p[0], "y": p[1]} if frame == "page"
                  else {"e": p[0], "n": p[1]})
            props.append(_prop(
                "gridiron", name, elev=elev, z_ref=z_ref, name=name,
                desc=f"{code}-{name}", code=code, layer=layer,
                rule="grid intersections", params={"source": "lines"},
                **kw))
    props.sort(key=lambda pr: _label_order(pr["name"]))
    return props


# ------------------------------------------------------------- wall corners --

def wall_corners(segs, inset_ft: float = 0.0, witness=None, *,
                 layer="Work", code="WP", elev=None, z_ref="FF",
                 merge_ft: float = 0.05) -> list:
    """Corner points from wall segments (brief section 4.2).

    ``segs`` are ``((x, y), (x, y))`` pairs straight from
    :func:`extrude.to_world` or :meth:`draft.DraftModel.wall_segments`
    (x = Easting, y = Northing, feet).  A corner is an endpoint shared by
    two or more segments (clustered within ``merge_ft``).  ``inset_ft``
    moves the point off the true corner along BOTH walls (a corner with
    exactly two legs; junctions with more legs stay put) — the
    true-point-unoccupiable case.  ``witness`` is an optional
    ``{"offset_ft": 2.0, "azimuth": 0.0}`` spec attached to every proposal
    for the commit step to hand to ``add_witness`` (one consistent
    side/distance per layer — the export lint enforces it)."""
    if witness is not None:
        witness = {"offset_ft": float(witness.get("offset_ft", 2.0)),
                   "azimuth": float(witness.get("azimuth", 0.0)) % 360.0}
    # cluster endpoints on a merge grid
    clusters: dict[tuple, dict] = {}
    for a, b in segs:
        for pt, other in ((a, b), (b, a)):
            key = (round(pt[0] / merge_ft), round(pt[1] / merge_ft))
            c = clusters.setdefault(key, {"pt": pt, "dirs": []})
            dx, dy = other[0] - pt[0], other[1] - pt[1]
            d = math.hypot(dx, dy)
            if d > 1e-9:
                c["dirs"].append((dx / d, dy / d))
    props = []
    idx = 0
    for key in sorted(clusters):
        c = clusters[key]
        if len(c["dirs"]) < 2:
            continue                              # a free end, not a corner
        x, y = c["pt"]
        if inset_ft and len(c["dirs"]) == 2:
            (u1x, u1y), (u2x, u2y) = c["dirs"]
            x += inset_ft * (u1x + u2x)
            y += inset_ft * (u1y + u2y)
        idx += 1
        pr = _prop(
            "wall_corners", f"C{idx:03d}", e=x, n=y, elev=elev,
            z_ref=z_ref, desc=(f"{code} CORNER"
                               + (f" INSET {inset_ft:g}FT" if inset_ft
                                  else "")),
            code=code, layer=layer, rule="wall corners",
            params={"inset_ft": float(inset_ft), "legs": len(c["dirs"])})
        if witness is not None:
            pr["witness"] = dict(witness)
        props.append(pr)
    return props


# --------------------------------------------------------------- along line --

def along_line(a, b, mode: str = "interval", *, stride_ft=None, n=None,
               inset_start: float = 0.0, inset_end: float = 0.0,
               remainder: str = "center", z_interp=None,
               layer="Mechanical", code="HGR", z_ref="FF",
               desc: str = "") -> list:
    """Points along a run (brief section 4.3).

    ``a``/``b`` are ``(x, y)`` = (E, N) feet.  Modes:

    * ``"interval"`` — fixed ``stride_ft`` spacing across the inset span;
      the leftover slack either splits evenly (``remainder="center"``) or
      accumulates at the far end (``remainder="end"``);
    * ``"divide"`` — ``n`` equal segments across the inset span (n+1
      points including both span ends).

    ``z_interp=(za, zb)`` interpolates Z LINEARLY between the endpoints of
    the FULL line a->b (drainage runs slope; never copy the start elevation
    down the run); ``None`` emits elevation-less points.  Points are
    numbered in walk order along the run (spool numbers mint at commit)."""
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    L = math.hypot(bx - ax, by - ay)
    if L <= 0:
        raise ValueError("along_line endpoints coincide")
    span = L - float(inset_start) - float(inset_end)
    if span < -1e-9:
        raise ValueError(f"insets ({inset_start} + {inset_end}) exceed the "
                         f"line length {L:.3f}")
    span = max(span, 0.0)
    ux, uy = (bx - ax) / L, (by - ay) / L

    if mode == "interval":
        if not stride_ft or float(stride_ft) <= 0:
            raise ValueError("interval mode needs a positive stride_ft "
                             "(see STRIDE_RULES / stride_for)")
        if remainder not in ("center", "end"):
            raise ValueError(f"unknown remainder {remainder!r}; expected "
                             "center | end")
        stride = float(stride_ft)
        k = int(math.floor(span / stride + 1e-9))
        slack = span - k * stride
        start = float(inset_start) + (slack / 2.0
                                      if remainder == "center" else 0.0)
        offsets = [start + i * stride for i in range(k + 1)]
        rule = f"interval {stride:g} ft, remainder {remainder}"
    elif mode == "divide":
        if not n or int(n) < 1:
            raise ValueError("divide mode needs n >= 1 equal segments")
        n = int(n)
        offsets = [float(inset_start) + i * span / n for i in range(n + 1)]
        rule = f"divide {n}"
    else:
        raise ValueError(f"unknown mode {mode!r}; expected interval | "
                         "divide")

    props = []
    params = {"a": (ax, ay), "b": (bx, by), "mode": mode,
              "stride_ft": stride_ft, "n": n,
              "inset_start": float(inset_start),
              "inset_end": float(inset_end), "remainder": remainder,
              "z_interp": tuple(z_interp) if z_interp else None}
    for i, d in enumerate(offsets):
        z = None
        if z_interp is not None:
            za, zb = float(z_interp[0]), float(z_interp[1])
            z = za + (zb - za) * (d / L)
        props.append(_prop(
            "along_line", f"{i:04d}", e=ax + ux * d, n=ay + uy * d,
            elev=z, z_ref=z_ref, desc=desc or f"{code} {i + 1}",
            code=code, layer=layer, rule=rule, params=params))
    return props


# -------------------------------------------------------------- offset line --

def offset_line(a, b, offset_ft: float, mode: str = "interval",
                **along_kw) -> list:
    """Points along a line OFFSET from a baseline (brief section 4.4).

    ``offset_ft`` is signed: positive = right of travel a->b, negative =
    left.  The offset line then runs through :func:`along_line` with the
    remaining parameters; descriptions carry the lath grammar
    (``O/S 5.00 -> ...``)."""
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    L = math.hypot(bx - ax, by - ay)
    if L <= 0:
        raise ValueError("offset_line endpoints coincide")
    ux, uy = (bx - ax) / L, (by - ay) / L
    rx, ry = uy, -ux                    # right of travel in (E, N) axes
    off = float(offset_ft)
    a2 = (ax + rx * off, ay + ry * off)
    b2 = (bx + rx * off, by + ry * off)
    props = along_line(a2, b2, mode, **along_kw)
    side = "R" if off >= 0 else "L"
    for pr in props:
        pr["desc"] = f"O/S {abs(off):.2f} {side} -> " + (pr["desc"] or "")
        pr["provenance"]["gen"] = "offset_line"
        pr["provenance"]["rule"] += f", offset {off:g} ft"
        pr["provenance"]["params"]["offset_ft"] = off
        pr["provenance"]["params"]["baseline"] = ((ax, ay), (bx, by))
    return props


# ---------------------------------------------------------------- bolt cage --

def _child_letters(i: int) -> str:
    """0 -> A ... 25 -> Z, 26 -> AA (bijective base-26)."""
    i += 1
    out = ""
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(ord("A") + r) + out
    return out


def bolt_cage(center, rows: int = 2, cols: int = 2,
              gauge_ns_in: float = 4.0, gauge_ew_in: float = 4.0,
              rot_deg: float = 0.0, *, name: str = "", elev=None,
              layer="Steel", code="AB", z_ref="TOS") -> list:
    """Rectangular bolt-pattern array (brief section 4.5).

    ``center`` is the column work point ``(x, y)`` = (E, N); gauges are in
    INCHES (common 4-22); ``rot_deg`` rotates the pattern clockwise from
    plan north (compass sense).  Returns the PARENT work point first
    (carrying the pattern params and :data:`BOLT_GROUP_NOTE`), then the
    children named ``<name>-A/-B/-C/-D...`` reading NW->NE, row by row.
    Children are computed from the parent — they drag as one rigid body;
    the field method is the template jig on the work point, never four
    independent stakes."""
    rows, cols = int(rows), int(cols)
    if rows < 1 or cols < 1:
        raise ValueError("bolt cage needs rows >= 1 and cols >= 1")
    cx, cy = float(center[0]), float(center[1])
    g_ns = float(gauge_ns_in) / 12.0
    g_ew = float(gauge_ew_in) / 12.0
    th = math.radians(float(rot_deg))
    c, s = math.cos(th), math.sin(th)
    cage = name or "CAGE"
    params = {"rows": rows, "cols": cols, "gauge_ns_in": float(gauge_ns_in),
              "gauge_ew_in": float(gauge_ew_in), "rot_deg": float(rot_deg),
              "center": (cx, cy)}
    parent = _prop(
        "bolt_cage", cage, e=cx, n=cy, elev=elev, z_ref=z_ref,
        name=name or None,
        desc=f"{code} GROUP WP {rows}x{cols} @ {gauge_ns_in:g}x"
             f"{gauge_ew_in:g}IN ROT {rot_deg:g}",
        code=code, layer=layer, rule="bolt cage work point", params=params)
    parent["note"] = BOLT_GROUP_NOTE
    props = [parent]
    for r in range(rows):
        for col in range(cols):
            # local offsets: row 0 = northmost, columns west -> east
            dn = ((rows - 1) / 2.0 - r) * g_ns
            de = (col - (cols - 1) / 2.0) * g_ew
            # clockwise-from-north pattern rotation
            n_rot = dn * c - de * s
            e_rot = dn * s + de * c
            i = r * cols + col
            suffix = "-" + _child_letters(i)
            child = _prop(
                "bolt_cage", f"{cage}{suffix}", e=cx + e_rot, n=cy + n_rot,
                elev=elev, z_ref=z_ref,
                name=(name + suffix) if name else None,
                desc=f"{code}{suffix} OF {cage}", code=code, layer=layer,
                rule="bolt cage child", params=params)
            child["parent"] = cage
            props.append(child)
    return props


# ------------------------------------------------------- line intersections --

def line_intersections(set_a, set_b, extend: bool = False,
                       dedupe_ft: float = 0.005, *, layer="Work",
                       code="WP", elev=None, z_ref="FF") -> list:
    """Every crossing between two pickable line sets (brief section 4.6).

    Set items are ``(a, b)`` segments or ``(label, a, b)`` labeled ones —
    points are ``(x, y)`` = (E, N).  ``extend=False`` takes true (on-
    segment) intersections only; ``extend=True`` takes apparent (infinite-
    line) ones too.  Crossings within ``dedupe_ft`` (default 1/16 in)
    collapse to one.  Names compose from the two source labels when both
    carry them (``A`` x ``1`` -> ``A1``), else stay ``None`` for
    spool-sequential numbering at commit."""
    def norm(item):
        if len(item) == 3:
            return str(item[0]), item[1], item[2]
        return "", item[0], item[1]

    hits = []
    for ia, item_a in enumerate(set_a):
        la, a1, a2 = norm(item_a)
        for ib, item_b in enumerate(set_b):
            lb, b1, b2 = norm(item_b)
            p = _seg_x(a1, a2, b1, b2, extend=extend)
            if p is None:
                continue
            name = _cell_name(la, lb) if la and lb else None
            hits.append((p, name, ia, ib))

    props = []
    kept: list[tuple] = []
    for p, name, ia, ib in hits:
        if any(math.hypot(p[0] - q[0], p[1] - q[1]) <= dedupe_ft
               for q in kept):
            continue
        kept.append(p)
        key = name if name else f"{ia}x{ib}"
        props.append(_prop(
            "line_intersections", key, e=p[0], n=p[1], elev=elev,
            z_ref=z_ref, name=name,
            desc=f"{code}-{name}" if name else code,
            code=code, layer=layer,
            rule="line-line intersections"
                 + (" (extended)" if extend else ""),
            params={"extend": bool(extend), "dedupe_ft": float(dedupe_ft)}))
    return props


# ------------------------------------------------------------ re-harvest ----

def _prov_key(prov) -> tuple | None:
    if not isinstance(prov, dict):
        return None
    gen = str(prov.get("gen", ""))
    key = str(prov.get("key", ""))
    return (gen, key) if gen and key else None


def _proposal_world(job, pr) -> tuple:
    if "n" in pr:
        return float(pr["n"]), float(pr["e"])
    # page-frame proposal: run it through the job frame
    from .fieldstitch import LayoutPoint
    tmp = LayoutPoint(id="_", x=float(pr["x"]), y=float(pr["y"]))
    n, e, _ = job.to_world(tmp)
    return n, e


def reharvest_diff(job, proposals, *, tol_ft: float = DIFF_TOL_FT) -> dict:
    """Model-change reconciliation (brief section 4.7): match re-generated
    proposals to the job's committed pins by provenance ``(gen, key)`` and
    bucket them:

    * ``"unchanged"`` — |delta| < ``tol_ft`` (1/16 in default):
      ``[{"point", "proposal"}]``;
    * ``"drifted"`` — same identity, moved: ``[{"point", "proposal",
      "dn", "de", "hd"(, "dz")}]`` — one-tap accept keeps the number and
      moves the pin;
    * ``"orphaned"`` — the source geometry is gone: the points, reported
      and NEVER auto-deleted (auto-deleting orphans erases scope-change
      evidence; keeping them silently stakes ghosts — flag and report);
    * ``"new"`` — proposals with no committed pin yet.

    Pure function: nothing is applied, renamed or deleted here."""
    by_key: dict[tuple, object] = {}
    for p in job.points:
        k = _prov_key(getattr(p, "provenance", None))
        if k is not None and k not in by_key:
            by_key[k] = p
    unchanged, drifted, new = [], [], []
    seen = set()
    for pr in proposals:
        k = _prov_key(pr.get("provenance"))
        if k is None or k not in by_key:
            new.append(pr)
            continue
        seen.add(k)
        p = by_key[k]
        pn, pe, pz = job.to_world(p)
        nn, ne = _proposal_world(job, pr)
        dn, de = nn - pn, ne - pe
        hd = math.hypot(dn, de)
        entry = {"point": p, "proposal": pr}
        dz = None
        if pr.get("elev") is not None and pz is not None:
            dz = float(pr["elev"]) - float(pz)
        if hd < tol_ft and not (dz and abs(dz) >= tol_ft):
            unchanged.append(entry)
        else:
            entry.update({"dn": dn, "de": de, "hd": hd})
            if dz is not None:
                entry["dz"] = dz
            drifted.append(entry)
    orphaned = [p for k, p in by_key.items() if k not in seen]
    return {"unchanged": unchanged, "drifted": drifted,
            "orphaned": orphaned, "new": new}
