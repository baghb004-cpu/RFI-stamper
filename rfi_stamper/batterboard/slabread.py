"""Slab reading: the slab edge from HEAVY linework, slab openings
(including ones whose cross is dashed), and curves with radius and
center.  Brief section 5.4.

Everything comes in through the Sightline, so a heavy line that is in the
file but not on the plotted sheet is already gone.  What that leaves is
the honest case this module exists for: an edge run with NO heavy plotted
line.  When the heavy outline has a gap and only a lighter line bridges
it, that run is returned as UNCONFIRMED.  It is never promoted to a
confident edge and never silently closed; the caller raises the open
item.  A gap that nothing bridges stays a gap.

The edge class is learned from the sheet (the heaviest line class that
carries enough length around the plan), never a hardcoded pen weight.
"""
from __future__ import annotations

import math

from .. import geom2d as g2
from . import model as M

SNAP_PT = 0.9                 # endpoints closer than this are one node
MIN_EDGE_SHARE = 0.5          # the edge class must carry this share of the grid perimeter
OPEN_MIN_FT, OPEN_MAX_FT = 0.5, 40.0
CROSS_SOLID, CROSS_DASHED = 0.9, 0.3


def _wkey(width: float) -> float:
    return round(width * 20.0) / 20.0


def edge_class(strokes, region, grid_perim_pt: float):
    """The heavy line class of the slab edge: ``(rgb, width)`` or None.

    Among black-ish line classes drawn inside ``region``, the HEAVIEST one
    whose total length is at least ``MIN_EDGE_SHARE`` of the grid
    perimeter."""
    tot: dict = {}
    for st in strokes:
        if not _inside(st.pts, region) or sum(st.rgb) > 3 * 110:
            continue
        key = (st.rgb, _wkey(st.width))
        tot[key] = tot.get(key, 0.0) + st.length
    cands = [k for k, v in tot.items() if v >= MIN_EDGE_SHARE * grid_perim_pt]
    if not cands:
        return None
    return max(cands, key=lambda k: k[1])


def _inside(pts, region) -> bool:
    return all(region[0] <= p[0] <= region[2] and region[1] <= p[1] <= region[3]
               for p in (pts[0], pts[-1]))


class _Graph:
    def __init__(self, snap: float = SNAP_PT):
        self.snap = snap
        self.nodes: list = []
        self.adj: dict = {}

    def node(self, p) -> int:
        for i, q in enumerate(self.nodes):
            if math.hypot(p[0] - q[0], p[1] - q[1]) <= self.snap:
                return i
        self.nodes.append((float(p[0]), float(p[1])))
        self.adj[len(self.nodes) - 1] = []
        return len(self.nodes) - 1

    def add(self, a, b, payload) -> None:
        i, j = self.node(a), self.node(b)
        if i == j:
            return
        self.adj[i].append((j, payload))
        self.adj[j].append((i, payload))


def _arc_of(st):
    """A curve stroke -> (center, radius) when it is a circular arc."""
    if st.curve is not None:
        got = g2.arc_from_bezier(*st.curve)
        if got is not None:
            return got
    cx, cy, r, rms = g2.fit_circle(st.pts)
    if r <= 0 or rms > max(0.02 * r, 0.1):
        return None
    return (cx, cy), r


def read_slab(strokes, frame: M.Frame, region, grid_perim_pt: float,
              prov: M.Provenance, contains=None) -> tuple:
    """-> ``(elements, remarks, edge_key)``.

    ``elements`` holds at most one ``slab_edge`` (the outline: ``poly`` is
    the world outline with arcs flattened, ``attrs["segments"]`` its runs,
    ``attrs["gaps"]`` the unbridged gaps) plus the ``slab_opening`` s.
    ``contains`` is a list of page points the outline must enclose (grid
    crossings) to be taken as THE slab."""
    remarks = []
    key = edge_class(strokes, region, grid_perim_pt)
    if key is None:
        remarks.append("no heavy line class carries enough length around the plan: "
                       "the slab edge was not read")
        return [], remarks, None
    heavy = [st for st in strokes
             if (st.rgb, _wkey(st.width)) == key and _inside(st.pts, region)]
    light = [st for st in strokes
             if st.curve is None and len(st.pts) == 2 and _inside(st.pts, region)
             and (st.rgb, _wkey(st.width)) != key and st.width < key[1]]
    g = _Graph()
    for st in heavy:
        payload = {"st": st, "kind": "arc" if st.curve is not None else "line"}
        g.add(st.pts[0], st.pts[-1], payload)

    # ---- components ------------------------------------------------------- #
    seen = set()
    comps = []
    for start in g.adj:
        if start in seen:
            continue
        stack, comp = [start], []
        while stack:
            u = stack.pop()
            if u in seen:
                continue
            seen.add(u)
            comp.append(u)
            stack.extend(v for v, _p in g.adj[u])
        comps.append(comp)

    def chain(comp):
        """Order a degree<=2 component into a path; returns (nodes, edges,
        closed)."""
        ends = [u for u in comp if len(g.adj[u]) == 1]
        if any(len(g.adj[u]) > 2 for u in comp):
            return None
        cur = ends[0] if ends else comp[0]
        nodes, edges, prev_edge = [cur], [], None
        while True:
            nxt = None
            for v, p in g.adj[cur]:
                if p is prev_edge:
                    continue
                nxt = (v, p)
                break
            if nxt is None:
                break
            v, p = nxt
            edges.append(p)
            prev_edge = p
            cur = v
            if cur == nodes[0]:
                return nodes, edges, True
            nodes.append(cur)
            if len(nodes) > len(comp) + 1:
                break
        return nodes, edges, False

    chains = [c for c in (chain(comp) for comp in comps) if c is not None]
    if len(chains) != len(comps):
        remarks.append("the heavy linework branches (three lines meet at a point): "
                       "the branching part was not read as slab edge")

    # ---- join open chains across gaps a lighter line bridges --------------- #
    loose = []
    for ci, (nodes, edges, closed) in enumerate(chains):
        if not closed and edges:
            loose.append((ci, 0))
            loose.append((ci, len(nodes) - 1))
    bridges = []
    used = set()
    for a in range(len(loose)):
        for b in range(a + 1, len(loose)):
            if a in used or b in used:
                continue
            pa = g.nodes[chains[loose[a][0]][0][loose[a][1]]]
            pb = g.nodes[chains[loose[b][0]][0][loose[b][1]]]
            for st in light:
                p, q = st.pts
                if ((math.hypot(p[0] - pa[0], p[1] - pa[1]) <= 1.5
                     and math.hypot(q[0] - pb[0], q[1] - pb[1]) <= 1.5)
                        or (math.hypot(q[0] - pa[0], q[1] - pa[1]) <= 1.5
                            and math.hypot(p[0] - pb[0], p[1] - pb[1]) <= 1.5)):
                    bridges.append((loose[a], loose[b], st))
                    used.add(a)
                    used.add(b)
                    break
    for (ca, ia), (cb, ib), st in bridges:
        na = chains[ca][0][ia]
        nb = chains[cb][0][ib]
        payload = {"st": st, "kind": "unconfirmed"}
        g.adj[na].append((nb, payload))
        g.adj[nb].append((na, payload))
    if bridges:
        seen.clear()
        comps = []
        for start in g.adj:
            if start in seen:
                continue
            stack, comp = [start], []
            while stack:
                u = stack.pop()
                if u in seen:
                    continue
                seen.add(u)
                comp.append(u)
                stack.extend(v for v, _p in g.adj[u])
            comps.append(comp)
        chains = [c for c in (chain(comp) for comp in comps) if c is not None]

    # ---- pick THE outline -------------------------------------------------- #
    def outline_of(nodes, edges, closed):
        pts = []
        segs = []
        m = len(edges)
        for k in range(m):
            a = g.nodes[nodes[k]]
            b = g.nodes[nodes[(k + 1) % len(nodes)]] if closed else g.nodes[nodes[k + 1]]
            p = edges[k]
            st = p["st"]
            if p["kind"] == "arc":
                arc = _arc_of(st)
                run = list(st.pts)
                if math.hypot(run[0][0] - a[0], run[0][1] - a[1]) > \
                        math.hypot(run[-1][0] - a[0], run[-1][1] - a[1]):
                    run = run[::-1]
                pts.extend(run[:-1])
                if arc is None:
                    segs.append({"kind": "curve", "a": a, "b": b, "pts": run})
                else:
                    segs.append({"kind": "arc", "a": a, "b": b, "center": arc[0],
                                 "radius": arc[1], "pts": run})
            else:
                pts.append(a)
                segs.append({"kind": p["kind"], "a": a, "b": b})
        if not closed:
            pts.append(g.nodes[nodes[-1]])
        return pts, segs

    best = None
    for nodes, edges, closed in chains:
        if not edges:
            continue
        pts, segs = outline_of(nodes, edges, closed)
        a = abs(g2.area(pts)) if len(pts) >= 3 else 0.0
        ok = True
        if contains and len(pts) >= 3:
            ok = sum(1 for c in contains if g2.point_in_poly(c[0], c[1], pts)) >= 1
        if ok and (best is None or a > best[0]):
            best = (a, pts, segs, closed)
    elements = []
    outline_page = None
    if best is None:
        remarks.append("heavy linework was found but no outline encloses the grid: "
                       "the slab edge was not read")
    else:
        _a, pts, segs, closed = best
        outline_page = pts if closed else None
        gaps = []
        if not closed:
            gaps.append({"a": frame.to_world(*pts[-1]), "b": frame.to_world(*pts[0]),
                         "page_a": pts[-1], "page_b": pts[0]})
        wsegs = []
        for s in segs:
            ws = {"kind": s["kind"], "a": frame.to_world(*s["a"]), "b": frame.to_world(*s["b"]),
                  "page_a": s["a"], "page_b": s["b"]}
            if s["kind"] == "arc":
                ws["center"] = frame.to_world(*s["center"])
                ws["radius_ft"] = s["radius"] * frame.ft_per_pt
                ws["page_center"] = s["center"]
                ws["pts"] = [frame.to_world(*p) for p in s["pts"]]
            elif s["kind"] == "curve":
                ws["pts"] = [frame.to_world(*p) for p in s["pts"]]
            wsegs.append(ws)
        wsegs = _merge_arcs(wsegs)
        p = M.Provenance(**prov.to_dict())
        n_unc = sum(1 for s in wsegs if s["kind"] == "unconfirmed")
        elements.append(M.Element(
            kind=M.SLAB_EDGE, poly=[frame.to_world(*q) for q in pts], page_poly=list(pts),
            closed=closed,
            attrs={"segments": wsegs, "gaps": gaps, "unconfirmed": n_unc,
                   "area_ft2": abs(g2.area([frame.to_world(*q) for q in pts])) if closed else None,
                   "pen": {"rgb": list(key[0]), "width_pt": key[1]}},
            prov=p, sources=[M.SRC_STRUCT]))

    # ---- slab openings ----------------------------------------------------- #
    elements.extend(_openings(strokes, frame, region, outline_page, prov, key))
    return elements, remarks, key


def _merge_arcs(segs) -> list:
    """Consecutive arc pieces of one circle are one arc."""
    out = []
    for s in segs:
        if (out and s["kind"] == "arc" and out[-1]["kind"] == "arc"
                and math.hypot(s["center"][0] - out[-1]["center"][0],
                               s["center"][1] - out[-1]["center"][1]) <= 0.02
                and abs(s["radius_ft"] - out[-1]["radius_ft"]) <= 0.02):
            out[-1]["b"] = s["b"]
            out[-1]["page_b"] = s["page_b"]
            out[-1]["pts"] = out[-1]["pts"] + s["pts"][1:]
        else:
            out.append(dict(s))
    return out


def rect_loops(strokes, region) -> tuple:
    """Rectangles formed by four visible lines meeting at their ends ->
    ``([(quad, edges)], lines)`` in viewer points."""
    lines = [st for st in strokes
             if st.curve is None and len(st.pts) == 2 and st.length >= 2.0
             and _inside(st.pts, region)]
    g = _Graph(SNAP_PT)
    for st in lines:
        g.add(st.pts[0], st.pts[1], st)
    found = {}
    for u in g.adj:
        for v, e1 in g.adj[u]:
            if v <= u:
                continue
            for w, e2 in g.adj[v]:
                if w == u:
                    continue
                for x, e3 in g.adj[w]:
                    if x in (u, v):
                        continue
                    for y, e4 in g.adj[x]:
                        if y != u:
                            continue
                        key = tuple(sorted((u, v, w, x)))
                        if key in found:
                            continue
                        quad = [g.nodes[u], g.nodes[v], g.nodes[w], g.nodes[x]]
                        if g2.is_rectangle(quad, 0.6):
                            found[key] = (quad, (e1, e2, e3, e4))
    return list(found.values()), lines


def _openings(strokes, frame, region, outline_page, prov, edge_key) -> list:
    """Rectangles that carry a cross (solid or dashed) -> slab openings."""
    found, lines = rect_loops(strokes, region)
    out = []
    for quad, edges in found:
        world = [frame.to_world(*p) for p in quad]
        r = g2.min_area_rect(world)
        if not (OPEN_MIN_FT <= r["width"] and r["length"] <= OPEN_MAX_FT):
            continue
        if outline_page is not None:
            c = g2.centroid(quad)
            if not g2.point_in_poly(c[0], c[1], outline_page):
                continue
        covers = []
        dashed = False
        for a, b in ((quad[0], quad[2]), (quad[1], quad[3])):
            cov, dsh = _diag_cover(a, b, lines, edges)
            covers.append(cov)
            dashed = dashed or dsh
        if min(covers) < CROSS_DASHED:
            continue                                   # a rectangle with no cross
        cross = "dashed" if (dashed or min(covers) < CROSS_SOLID) else "solid"
        p = M.Provenance(**prov.to_dict())
        out.append(M.Element(
            kind=M.SLAB_OPENING, poly=world, page_poly=list(quad),
            attrs={"cross": cross, "width_ft": r["width"], "length_ft": r["length"],
                   "cross_cover": round(min(covers), 3)},
            prov=p, sources=[M.SRC_STRUCT]))
    return out


def _diag_cover(a, b, lines, edges) -> tuple:
    ln = math.hypot(b[0] - a[0], b[1] - a[1])
    if ln <= 0:
        return 0.0, False
    ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
    iv = []
    dashed = False
    for st in lines:
        if any(st is e for e in edges):
            continue
        p, q = st.pts
        if g2.dist_point_line(p, a, b) > 1.0 or g2.dist_point_line(q, a, b) > 1.0:
            continue
        t0 = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
        t1 = (q[0] - a[0]) * ux + (q[1] - a[1]) * uy
        lo, hi = max(0.0, min(t0, t1)), min(ln, max(t0, t1))
        if hi - lo <= 0:
            continue
        iv.append((lo, hi))
        if st.dashes and not st.dashes.replace(" ", "").startswith("[]"):
            dashed = True
    iv.sort()
    tot, end = 0.0, 0.0
    for lo, hi in iv:
        lo = max(lo, end)
        if hi > lo:
            tot += hi - lo
            end = hi
    if len(iv) > 2:
        dashed = dashed or tot / ln < CROSS_SOLID
    return tot / ln, dashed


def edge_points(edge: M.Element) -> dict:
    """The stakeable points of a slab outline:
    ``{"corners": [(e, n, note)], "radius_points": [(e, n, radius_ft)]}``.

    A corner is a vertex where two CONFIRMED runs meet at an angle; the
    ends of an arc are its tangent points; an end of an unconfirmed run or
    of a gap is reported with its note so the caller can hold it."""
    segs = edge.attrs.get("segments", [])
    corners, radius = [], []
    n = len(segs)
    if not n:
        return {"corners": corners, "radius_points": radius}
    rng = range(n) if edge.closed else range(1, n)
    for i in rng:
        a, b = segs[i - 1], segs[i]
        pt = b["a"]
        note = "corner"
        if "arc" in (a["kind"], b["kind"]):
            note = "tangent point"
        if "unconfirmed" in (a["kind"], b["kind"]):
            note = "end of an edge run with no heavy plotted line"
        elif a["kind"] == b["kind"] == "line":
            ux, uy = a["b"][0] - a["a"][0], a["b"][1] - a["a"][1]
            vx, vy = b["b"][0] - b["a"][0], b["b"][1] - b["a"][1]
            cr = abs(ux * vy - uy * vx)
            if cr <= math.sin(math.radians(2.0)) * math.hypot(ux, uy) * math.hypot(vx, vy):
                continue                               # a straight joint, not a corner
        corners.append((pt[0], pt[1], note))
    if not edge.closed:
        corners.insert(0, (segs[0]["a"][0], segs[0]["a"][1], "end of the slab edge at a gap"))
        corners.append((segs[-1]["b"][0], segs[-1]["b"][1], "end of the slab edge at a gap"))
    for s in segs:
        if s["kind"] == "arc":
            radius.append((s["center"][0], s["center"][1], s["radius_ft"]))
    return {"corners": corners, "radius_points": radius}
