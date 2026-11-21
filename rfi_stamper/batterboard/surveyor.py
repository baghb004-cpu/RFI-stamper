"""The Surveyor hat: control request, setup plan, check forms.  Brief
section 8.

* **Control request**: requested control locations on structure that will
  still be there later (on the slab, clear of concrete and openings),
  each with an id, ties to two gridlines and coordinates in the declared
  frame.  The wording stays neutral: these are REQUESTED control, never a
  claim about how any crew works.
* **Setup plan**, built on ``fieldpro.station_geometry``.  A layout point
  is served by a setup only when it is inside the working range (a USER
  setting, never printed as a fact about an instrument), no farther out
  than the farthest control that setup uses, seen along a clear line of
  sight, and the setup sees a control pair inside the existing triangle
  rule.  Lines of sight are blocked by the walls and columns standing at
  the chosen construction stage.  A point with no valid setup is FLAGGED
  and listed, never hidden.
* **Check forms**: the existing as-staked ledger, and a slab elevation
  survey form only when a general note calls for one, carrying that
  note's exact quote from its proof record.
"""
from __future__ import annotations

import math
import os

from .. import fieldpro
from .. import geom2d as g2
from .. import papertrail as pt
from . import model as M
from . import notes as notes_mod
from . import platekit as pk

CONTROL_OFFSET_FT = 2.0        # a requested control sits this far off a grid crossing
MIN_ANGLE, MAX_ANGLE = 45.0, 135.0

pt.register("SV-CONTROL", "summary",
            "Requested control on {v:level_name}: {c:requested control point:total}, each on the "
            "slab, clear of concrete and openings, tied to its two nearest gridlines.")
pt.register("SV-SETUP", "summary",
            "Setup plan for {v:level_name}, {v:stage_text}: {c:setup:total} "
            "{s:setup:total:serves|serve} {n:layout point:served}. {v:range_text} "
            "Lines of sight are blocked by {v:blockers}. {not_compared}")
pt.register("SV-SURVEY", "summary", "A slab elevation survey is called for: {q:0}")


def _obstacles(lv: M.Level, stage: str) -> list:
    """What stands on the level at the chosen stage."""
    if stage == "pre":
        return []
    out = [e.poly for e in lv.of(M.WALL, M.COLUMN, M.CONCRETE)]
    out += [e.poly for e in lv.of(M.ROUND_COLUMN)]
    return out


def _clear(a, b, obstacles) -> bool:
    """True when the sight line a-b crosses no obstacle."""
    for poly in obstacles:
        x0, y0, x1, y1 = g2.bbox(poly)
        if max(a[0], b[0]) < x0 or min(a[0], b[0]) > x1 or max(a[1], b[1]) < y0 \
                or min(a[1], b[1]) > y1:
            continue
        n = len(poly)
        for i in range(n):
            if g2.seg_intersection(a, b, poly[i], poly[(i + 1) % n]) is not None:
                # touching the target's own corner is not a blocked sight
                hit = g2.seg_intersection(a, b, poly[i], poly[(i + 1) % n])
                if math.hypot(hit[0] - b[0], hit[1] - b[1]) > 0.05 and \
                        math.hypot(hit[0] - a[0], hit[1] - a[1]) > 0.05:
                    return False
    return True


def _free(lv: M.Level, p, clearance: float = 1.0) -> bool:
    """On the slab and clear of concrete and openings."""
    edge = next(iter(lv.of(M.SLAB_EDGE)), None)
    if edge is not None and edge.closed and not g2.point_in_poly(p[0], p[1], edge.poly):
        return False
    if edge is not None and edge.closed and g2.dist_point_poly(p, edge.poly) < clearance:
        return False
    for e in lv.of(M.WALL, M.COLUMN, M.CONCRETE, M.ROUND_COLUMN, M.SLAB_OPENING,
                   M.OVERLAY_OPENING, M.MASONRY):
        if len(e.poly) >= 3 and (g2.point_in_poly(p[0], p[1], e.poly)
                                 or g2.dist_point_poly(p, e.poly) < clearance):
            return False
    return True


def control_request(lv: M.Level, names) -> list:
    """Requested control for a level: ``[{"id", "e", "n", "ties"}]``.

    One beside each of the grid crossings nearest the four corners of the
    grid and one beside the crossing nearest its middle, each offset off
    the crossing (a crossing is where a column usually stands) to the
    first free place on the slab."""
    xs, ys = lv.grid.family("x"), lv.grid.family("y")
    if len(xs) < 2 or len(ys) < 2:
        return []
    cross = {name: (e, n) for name, e, n in lv.grid.intersections()}
    picks = [f"{xs[0].name}/{ys[0].name}", f"{xs[-1].name}/{ys[0].name}",
             f"{xs[-1].name}/{ys[-1].name}", f"{xs[0].name}/{ys[-1].name}",
             f"{xs[len(xs) // 2].name}/{ys[len(ys) // 2].name}"]
    cx = sum(p[0] for p in cross.values()) / len(cross)
    cy = sum(p[1] for p in cross.values()) / len(cross)
    out = []
    for name in dict.fromkeys(picks):
        e0, n0 = cross[name]
        sx = 1.0 if e0 <= cx else -1.0
        sy = 1.0 if n0 <= cy else -1.0
        spot = None
        for k in (1, 2, 3, 4):
            for dx, dy in ((sx, sy), (sx, -sy), (-sx, sy), (-sx, -sy)):
                p = (e0 + dx * CONTROL_OFFSET_FT * k, n0 + dy * CONTROL_OFFSET_FT * k)
                if _free(lv, p):
                    spot = p
                    break
            if spot:
                break
        if spot is None:
            continue
        e, n = round(spot[0], 3), round(spot[1], 3)
        num = names.number(lv.code, "control", e, n, start=1, tol=0.05)
        out.append({"id": f"RC-{lv.code}-{num:02d}", "e": e, "n": n,
                    "ties": lv.grid.tie_text(e, n)})
    return out


def setup_plan(lv: M.Level, controls: list, rows: list, opts: M.Options) -> dict:
    """Choose setups that serve the layout points.

    Returns ``{"setups": [{"id", "e", "n", "controls", "points", "advice"}],
    "unserved": [PointRow], "served": n, "total": n, "range": ft | None}``."""
    stage = opts.stage if opts.stage in ("pre", "post") else "post"
    obstacles = _obstacles(lv, stage)
    rng = opts.working_range_ft
    pts = [r for r in rows if r.family != "grid"]
    # candidate setups: the middle of every grid bay, when it is free
    xs, ys = lv.grid.family("x"), lv.grid.family("y")
    cands = []
    for a, b in zip(xs, xs[1:]):
        for c, d in zip(ys, ys[1:]):
            pa = g2.line_intersection(a.world_a, a.world_b, c.world_a, c.world_b)
            pb = g2.line_intersection(b.world_a, b.world_b, d.world_a, d.world_b)
            if pa is None or pb is None:
                continue
            mid = ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0)
            for off in ((0, 0), (3, 3), (-3, 3), (3, -3), (-3, -3)):
                p = (mid[0] + off[0], mid[1] + off[1])
                if _free(lv, p):
                    cands.append(p)
                    break
    scored = []
    for s in cands:
        seen = [c for c in controls if _clear(s, (c["e"], c["n"]), obstacles)]
        if len(seen) < 2:
            continue
        ok_pair = False
        for i in range(len(seen)):
            for j in range(i + 1, len(seen)):
                va = (seen[i]["e"] - s[0], seen[i]["n"] - s[1])
                vb = (seen[j]["e"] - s[0], seen[j]["n"] - s[1])
                la, lb = math.hypot(*va), math.hypot(*vb)
                if la <= 0 or lb <= 0:
                    continue
                ang = math.degrees(math.acos(max(-1.0, min(1.0, (va[0] * vb[0] + va[1] * vb[1])
                                                            / (la * lb)))))
                if MIN_ANGLE <= ang <= MAX_ANGLE:
                    ok_pair = True
        if not ok_pair:
            continue
        far = max(math.hypot(c["e"] - s[0], c["n"] - s[1]) for c in seen)
        serves = set()
        for r in pts:
            d = math.hypot(r.e - s[0], r.n - s[1])
            if d > far + 1e-6:
                continue                              # farther out than its farthest control
            if rng is not None and d > rng + 1e-6:
                continue                              # outside the working range
            if not _clear(s, (r.e, r.n), obstacles):
                continue
            serves.add(r.name)
        scored.append((s, seen, serves))
    chosen, covered = [], set()
    while True:
        best = max(scored, key=lambda t: len(t[2] - covered), default=None)
        if best is None or not (best[2] - covered):
            break
        s, seen, serves = best
        mine = sorted(serves - covered)
        covered |= serves
        by_name = {r.name: r for r in pts}
        advice = fieldpro.station_geometry(
            (s[1], s[0]), [(c["id"], c["n"], c["e"]) for c in seen],
            [(by_name[n].n, by_name[n].e) for n in mine])
        chosen.append({"id": f"SU-{lv.code}-{len(chosen) + 1:02d}", "e": round(s[0], 3),
                       "n": round(s[1], 3), "controls": [c["id"] for c in seen],
                       "points": mine, "advice": advice["verdict"],
                       "advice_notes": [pt.plain(x) for x in advice["notes"]]})
    unserved = [r for r in pts if r.name not in covered]
    return {"setups": chosen, "unserved": unserved, "served": len(pts) - len(unserved),
            "total": len(pts), "range": rng, "stage": stage, "obstacles": len(obstacles)}


def plates_extra(rr, reg, trail, points, names, opts: M.Options, results: dict):
    """-> the ``extra`` callback for ``plates.build``: adds the control
    request plate and the setup plan plate of each level."""
    from . import plates as plates_mod

    def extra(ps, lv, i, start, band, listing):
        lp = points.get(lv.code)
        controls = control_request(lv, names)
        res = results.setdefault(lv.code, {})
        res["controls"] = controls
        if not controls:
            res["note"] = "requested control could not be placed (the grid was not read both ways)"
            return
        # ---- control request ---------------------------------------------------- #
        proof = trail.add("SV-CONTROL", level=lv.code,
                          classes={"requested control point": {
                              "noun": ["requested control point", "requested control points"],
                              "total": len(controls), "compared": len(controls)}},
                          values={"level_name": pt.Verbatim(lv.name)}, result="info")
        pl, cont = start("7", "CONTROL REQUEST")
        plates_mod._base(pl, lv, light=True)
        cols = [(pk.label("REQUESTED CONTROL"), 1.8, "l"), (pk.label("TIES"), 3.4, "l"),
                (pk.label("NORTHING"), 1.4, "r"), (pk.label("EASTING"), 1.4, "r")]
        rows = [[pk.data(c["id"], src=f"control:{c['id']}"),
                 pk.data(c["ties"], src=f"control:{c['id']}"),
                 pk.data(f"{c['n']:.3f}", src=f"control:{c['id']}"),
                 pk.data(f"{c['e']:.3f}", src=f"control:{c['id']}")] for c in controls]

        def draw(p, controls=controls):
            for c in controls:
                p.marker(c["e"], c["n"], "control")
                p.note(c["e"], c["n"], [pk.data(c["id"], src=f"control:{c['id']}"),
                                        pk.data(c["ties"], src=f"control:{c['id']}")])

        plates_mod._table(ps, pl, cont, cols, rows, draw,
                          band(pk.statement(trail.say(proof.id))))
        # ---- setup plan ----------------------------------------------------------- #
        if lp is None or not lp.rows:
            return
        plan = setup_plan(lv, controls, lp.rows, opts)
        res["plan"] = plan
        stage_text = ("before the pour" if plan["stage"] == "pre" else "after the pour")
        blockers = ("nothing; at this stage nothing stands on the level" if not plan["obstacles"]
                    else "the walls and columns of this level")
        range_text = (f"Working range (a user setting): {M.fmt_ftin(plan['range'])}."
                      if plan["range"] is not None
                      else "A working range was not set, so range was not checked.")
        proof = trail.add(
            "SV-SETUP", level=lv.code,
            classes={"setup": {"noun": ["setup", "setups"], "total": len(plan["setups"]),
                               "compared": len(plan["setups"])},
                     "layout point": {"noun": ["layout point", "layout points"],
                                      "total": plan["total"], "compared": plan["total"],
                                      "served": plan["served"]}},
            values={"level_name": pt.Verbatim(lv.name), "stage_text": stage_text,
                    "range_text": range_text, "blockers": blockers},
            not_compared=(["grid crossings (control, not layout)"]), result=(
                "flag" if plan["unserved"] else "info"))
        pl, cont = start("8", "SETUP PLAN")
        plates_mod._base(pl, lv, light=True)
        cols = [(pk.label("SETUP"), 1.3, "l"), (pk.label("NORTHING"), 1.2, "r"),
                (pk.label("EASTING"), 1.2, "r"), (pk.label("REQUESTED CONTROL IN SIGHT"), 3.0, "l"),
                (pk.label("POINTS"), 0.9, "r"), (pk.label("GEOMETRY ADVICE"), 1.6, "l")]
        rows = [[pk.data(s["id"], src=f"setup:{s['id']}"),
                 pk.data(f"{s['n']:.3f}", src=f"setup:{s['id']}"),
                 pk.data(f"{s['e']:.3f}", src=f"setup:{s['id']}"),
                 pk.data(", ".join(s["controls"]), src=f"setup:{s['id']}"),
                 pk.data(str(len(s["points"])), src=f"setup:{s['id']}"),
                 pk.data(s["advice"], src=f"setup:{s['id']}")] for s in plan["setups"]]
        for r in plan["unserved"]:
            rows.append([pk.data("NOT SERVED", src=f"point:{r.name}"),
                         pk.data(f"{r.n:.3f}", src=f"point:{r.name}"),
                         pk.data(f"{r.e:.3f}", src=f"point:{r.name}"),
                         pk.data(r.name, src=f"point:{r.name}"),
                         pk.data("", src=f"point:{r.name}"),
                         pk.data("flagged", src=f"point:{r.name}")])

        def draw2(p, plan=plan, controls=controls):
            for c in controls:
                p.marker(c["e"], c["n"], "control")
                p.tag(c["e"], c["n"], pk.data(c["id"], src=f"control:{c['id']}"))
            for s in plan["setups"]:
                p.marker(s["e"], s["n"], "radius")
                p.note(s["e"], s["n"], [pk.data(s["id"], src=f"setup:{s['id']}"),
                                        pk.data(f"{len(s['points'])} points",
                                                src=f"setup:{s['id']}")])
            for r in plan["unserved"]:
                p.marker(r.e, r.n, "point", accent=True)
                p.tag(r.e, r.n, pk.data(r.name, src=f"point:{r.name}"), accent=True)

        plates_mod._table(ps, pl, cont, cols, rows, draw2,
                          band(pk.statement(trail.say(proof.id))))

    return extra


def control_csv(results: dict, rr, path: str) -> int:
    """The requested control as a point CSV (id, N, E, blank Z, ties)."""
    from ..fsutil import atomic_write_bytes
    lines = ["Point,Northing,Easting,Elevation,Description"]
    n = 0
    for code in sorted(results):
        for c in results[code].get("controls", []):
            lines.append(f"{c['id']},{c['n']:.3f},{c['e']:.3f},,REQUESTED CONTROL "
                         f"{c['ties'].replace(',', ';')}")
            n += 1
    atomic_write_bytes(("\n".join(lines) + "\n").encode("utf-8"), path)
    return n


def survey_form(path: str, rr, trail, opts: M.Options):
    """The slab elevation survey form, ONLY when a general note calls for
    one; it carries that note's exact quote.  Returns the kit manifest, or
    None when no note asks for a survey."""
    need = notes_mod.survey_required(rr.notes)
    if not need:
        return None
    note = need[0]
    q = note.quote()
    proof = trail.add("SV-SURVEY", quotes=[q], values={"sheet": note.sheet}, result="info")
    ps = pk.PlateSet(sheet="ANSI B", project=opts.project, issue=opts.issue)
    n_plates = 0
    for lv in rr.levels:
        if lv.blocked or lv.frame is None:
            continue
        n_plates += 1
        pl = ps.plate(f"BB-9{n_plates:02d}", f"{lv.name} SLAB ELEVATION SURVEY")
        cols = [(pk.label("GRID CROSSING"), 1.4, "l"), (pk.label("NORTHING"), 1.2, "r"),
                (pk.label("EASTING"), 1.2, "r"), (pk.label("DESIGN ELEVATION"), 1.6, "l"),
                (pk.label("MEASURED"), 1.4, "l"), (pk.label("DIFFERENCE"), 1.4, "l"),
                (pk.label("BY / DATE"), 1.6, "l")]
        rows = [[pk.data(name, src="grid"), pk.data(f"{n:.3f}", src="grid"),
                 pk.data(f"{e:.3f}", src="grid"), pk.data("", src="grid"),
                 pk.data("", src="grid"), pk.data("", src="grid"), pk.data("", src="grid")]
                for name, e, n in lv.grid.intersections()]
        pl.table(cols, rows, where="full")
        st = [pk.statement(trail.say(proof.id))]
        if lv.frame is not None:
            st.append(pk.data(lv.frame.label, src="frame"))
        st.append(pk.data("Design elevations are not laid out by this run; fill them in "
                          "from the drawings.", src="scope"))
        pl.statements(st)
    if not n_plates:
        return None
    man = ps.save(path)
    man["quote"] = f'"{q.text}" ({q.cite()})'
    return man


def ledger(path: str, lp, lv: M.Level, opts: M.Options) -> bool:
    """The existing as-staked ledger, as a blank form for this level's
    job (rows fill in as points are staked)."""
    if lp is None or lp.job is None:
        return False
    qa = fieldpro.QAStore()
    proj = (opts.project or {}).get("project", "")
    fieldpro.ledger_pdf(lp.job, qa, path, project=proj, area=f"{lv.name} {lv.struct.sheet}",
                        log=lambda *_: None)
    return os.path.exists(path)
