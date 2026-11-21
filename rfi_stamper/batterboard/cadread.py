"""The CAD background: the second source, and the declared frame.

Reads an ASCII DXF (through ``dxfread``) into the same element model the
plan reader fills: a named grid, concrete walls and columns, round
columns, masonry.  Coordinates are the file's OWN origin and DECLARED
units, converted to decimal feet -- nothing is shifted, rotated or
re-based here (brief section 8, "Frame").

What is read, and what is not, is decided by layer NAME conventions only
(``dxfread.classify_layers``); geometry is never used to guess a layer's
role.  Coverage-honest: wall-layer linework that is not a closed shape is
counted and reported, never silently dropped, and layers of no known role
are listed.

Every element from here carries ``method = CAD`` and the single source
tag ``CAD``; whether the structural plan confirms it is the cross-check's
question, not this module's.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from .. import dxfread
from .. import geom2d as g2
from . import gridread
from . import model as M


@dataclass
class CadBackground:
    path: str = ""
    units: str = ""                   # as declared ("" = none declared)
    feet: float = 0.0                 # feet per drawing unit actually used
    roles: dict = field(default_factory=dict)      # role -> [layer names]
    grid: M.Grid = field(default_factory=M.Grid)   # world ends in CAD feet
    elements: list = field(default_factory=list)   # [Element], unnumbered
    skipped: dict = field(default_factory=dict)    # dxfread's tally
    problems: list = field(default_factory=list)   # plain-words remarks
    unread: dict = field(default_factory=dict)     # role -> count of entities not turned into elements

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def of(self, *kinds) -> list:
        return [e for e in self.elements if e.kind in kinds]


def read_cad(path: str, units: str | None = None) -> CadBackground:
    """Read a CAD background.  ``units`` is used only when the file
    declares none (the caller's explicit setting); with neither, the file
    cannot be used and ``problems`` says so.  Raises ``dxfread.DxfError``
    for anything that is not ASCII DXF."""
    doc = dxfread.read(path)
    bg = CadBackground(path=path, units=doc.units, skipped=dict(doc.skipped))
    try:
        bg.feet = doc.feet(units)
    except dxfread.DxfError as e:
        bg.problems.append(str(e))
        return bg
    bg.roles = dxfread.classify_layers(doc)
    prov = M.Provenance(file=os.path.basename(path), page=0, sheet="",
                        method=M.CAD, confidence=1.0, note="from the CAD background")
    f = bg.feet

    def ft(p):
        return (p[0] * f, p[1] * f)

    # ---- grid ------------------------------------------------------------ #
    g_layers = bg.roles.get("grid", [])
    lines = []
    texts = []
    for e in doc.on(*g_layers):
        if e.kind == "LINE" or (e.kind == "POLYLINE" and len(e.pts) == 2):
            a, b = ft(e.pts[0]), ft(e.pts[1])
            ln = math.hypot(b[0] - a[0], b[1] - a[1])
            if ln > 0:
                lines.append({"a": a, "b": b, "length": ln})
        elif e.kind == "TEXT" and e.text.strip():
            texts.append((ft(e.pts[0]), e.text.strip().upper(), e.height * f))
    named = []
    for ln in lines:
        best = None
        for (tp, name, h) in texts:
            if not gridread.NAME_RE.match(name):
                continue
            d = min(math.hypot(tp[0] - ln["a"][0], tp[1] - ln["a"][1]),
                    math.hypot(tp[0] - ln["b"][0], tp[1] - ln["b"][1]))
            off = g2.dist_point_line(tp, ln["a"], ln["b"])
            reach = max(4.0 * max(h, 0.5), 0.08 * ln["length"])
            if d <= reach and off <= max(2.0 * max(h, 0.5), 1.5):
                if best is None or d < best[0]:
                    best = (d, name)
        if best is not None:
            named.append(dict(ln, name=best[1], bubble=None))
    if g_layers:
        grid, problems, _axes = gridread.build_grid(named, 1.0, y_up=True)
        for g in grid.lines:
            g.world_a, g.world_b = g.page_a, g.page_b      # already world feet
            g.page_a = g.page_b = (0.0, 0.0)
        bg.grid = grid
        bg.problems.extend("CAD background: " + p for p in problems)
        if len(lines) != len(named):
            bg.unread["grid"] = len(lines) - len(named)
    else:
        bg.problems.append("CAD background: no grid layer was recognized by name")

    # ---- shapes ---------------------------------------------------------- #
    def closed_polys(layers, role):
        out = []
        loose = 0
        for e in doc.on(*layers):
            if e.kind == "POLYLINE" and e.closed and len(e.pts) >= 3:
                if any(abs(b) > 1e-9 for b in (e.bulges or [])):
                    loose += 1                      # curved polyline: not read yet
                    continue
                out.append([ft(p) for p in e.pts])
            elif e.kind in ("LINE", "POLYLINE", "ARC"):
                loose += 1
        if loose:
            bg.unread[role] = bg.unread.get(role, 0) + loose
        return out

    for poly in closed_polys(bg.roles.get("wall", []), "wall"):
        poly = g2.drop_collinear(poly, 1e-6)
        r = g2.min_area_rect(poly)
        kind = M.WALL
        bg.elements.append(M.Element(
            kind=kind, poly=poly, prov=prov, sources=[M.SRC_CAD],
            attrs={"length_ft": r["length"], "thickness_ft": _thickness(poly)}))
    col_layers = bg.roles.get("column", [])
    for poly in closed_polys(col_layers, "column"):
        poly = g2.drop_collinear(poly, 1e-6)
        r = g2.min_area_rect(poly)
        bg.elements.append(M.Element(
            kind=M.COLUMN, poly=poly, prov=prov, sources=[M.SRC_CAD],
            attrs={"length_ft": r["length"], "thickness_ft": r["width"]}))
    for e in doc.on(*col_layers):
        if e.kind == "CIRCLE" and e.center is not None and e.radius:
            c = ft(e.center)
            r = e.radius * f
            bg.elements.append(M.Element(
                kind=M.ROUND_COLUMN, center=c, radius_ft=r, poly=_ngon(c, r),
                prov=prov, sources=[M.SRC_CAD], attrs={"diameter_ft": 2 * r}))
    for poly in closed_polys(bg.roles.get("masonry", []), "masonry"):
        poly = g2.drop_collinear(poly, 1e-6)
        r = g2.min_area_rect(poly)
        bg.elements.append(M.Element(
            kind=M.MASONRY, poly=poly, prov=prov, sources=[M.SRC_CAD],
            attrs={"length_ft": r["length"], "thickness_ft": _thickness(poly)}))
    for role, n in sorted(bg.unread.items()):
        bg.problems.append(
            f"CAD background: {n} {role}-layer item(s) are not closed shapes "
            "(or carry no name) and were not read")
    return bg


def _ngon(c, r, n: int = 48) -> list:
    return [(c[0] + r * math.cos(2 * math.pi * k / n),
             c[1] + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _thickness(poly) -> float:
    """Thickness as drawn: the shortest inward crossing from the middle of
    any face that is at least as long as that crossing."""
    from .concrete import thickness_of
    return thickness_of(poly)


def level_token(name: str) -> str:
    """The level a CAD file name declares (``background_L2.dxf`` -> L2),
    read with the same grammar as sheet titles; "" when it names none."""
    from .intake import level_from_text
    stem = os.path.splitext(os.path.basename(name))[0]
    lv = level_from_text(stem.replace("_", " ").replace("-", " "))
    return lv[1] if lv else ""
