"""Openings by others: mechanical openings from shop drawing overlays and
penetrations noted on the plan.  Brief section 5.6.

An overlay is registered to the structure through the GRID NAMES the shop
drawing shares with the structural plan, never by eye.  Even so its
locations are APPROXIMATE (a shop drawing is drawn by another trade, to
another purpose) and every element from here says so: confidence 0.5, an
``approximate`` attribute, and a provenance note that rides every output.
"""
from __future__ import annotations

import math
import re

from .. import geom2d as g2
from . import gridread, slabread
from . import model as M

OPENING_WORD = re.compile(r"\b(OPENING|OPNG|OPENINGS)\b", re.I)
PEN_WORD = re.compile(r"\b(CORE|CORED|SLEEVE|SLEEVES|PENETRATION|BLOCKOUT)\b", re.I)
LABEL_REACH_FT = 2.0
PEN_REACH_FT = 4.0
APPROX = "approximate: located from a shop drawing overlay"


def read_overlay(strokes, words, lines, frame: M.Frame, region, prov: M.Provenance) -> list:
    """Rectangles on a shop drawing that carry an opening label ->
    unnumbered ``overlay_opening`` elements (world feet)."""
    rects, _lines = slabread.rect_loops(strokes, region)
    out = []
    labels = [ln for ln in lines if OPENING_WORD.search(ln["text"])]
    for quad, _edges in rects:
        world = [frame.to_world(*p) for p in quad]
        r = g2.min_area_rect(world)
        if r["width"] < 0.25 or r["length"] > 40.0:
            continue
        best = None
        for ln in labels:
            c = frame.to_world((ln["bbox"][0] + ln["bbox"][2]) / 2.0,
                               (ln["bbox"][1] + ln["bbox"][3]) / 2.0)
            d = 0.0 if g2.point_in_poly(c[0], c[1], world) else g2.dist_point_poly(c, world)
            if d <= LABEL_REACH_FT and (best is None or d < best[0]):
                best = (d, ln["text"].strip())
        if best is None:
            continue
        p = M.Provenance(**prov.to_dict())
        p.confidence = 0.5
        p.note = APPROX
        out.append(M.Element(
            kind=M.OVERLAY_OPENING, poly=world, page_poly=[],
            attrs={"approximate": True, "label": best[1], "width_ft": r["width"],
                   "length_ft": r["length"]},
            prov=p, sources=[M.SRC_OVERLAY]))
    return out


def read_penetrations(strokes, lines, frame: M.Frame, region, prov: M.Provenance) -> list:
    """Penetrations NOTED on the structural plan (a note such as a core or
    a sleeve beside a small circle) -> unnumbered ``penetration``
    elements.  The location is the circle's center when one is within
    reach of the note, else the note itself (approximate)."""
    circles = [c for c in gridread.find_circles(strokes, r_min=0.8, r_max=30.0)]
    out = []
    for ln in lines:
        if not PEN_WORD.search(ln["text"]):
            continue
        cx = (ln["bbox"][0] + ln["bbox"][2]) / 2.0
        cy = (ln["bbox"][1] + ln["bbox"][3]) / 2.0
        if not (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]):
            continue
        tw = frame.to_world(cx, cy)
        best = None
        for c in circles:
            cw = frame.to_world(*c["center"])
            # distance from the note's nearest edge, not its middle
            dx = max(ln["bbox"][0] - c["center"][0], 0.0, c["center"][0] - ln["bbox"][2])
            dy = max(ln["bbox"][1] - c["center"][1], 0.0, c["center"][1] - ln["bbox"][3])
            d = math.hypot(dx, dy) * frame.ft_per_pt
            if d <= PEN_REACH_FT and (best is None or d < best[0]):
                best = (d, cw, c["r"] * frame.ft_per_pt)
        p = M.Provenance(**prov.to_dict())
        attrs = {"text": ln["text"].strip()}
        if best is None:
            p.confidence = 0.5
            attrs["approximate"] = True
            p.note = "approximate: located from its note"
            out.append(M.Element(kind=M.PENETRATION, center=tw, attrs=attrs, prov=p,
                                 sources=[M.SRC_STRUCT]))
        else:
            attrs["diameter_ft"] = 2.0 * best[2]
            out.append(M.Element(kind=M.PENETRATION, center=best[1], radius_ft=best[2],
                                 attrs=attrs, prov=p, sources=[M.SRC_STRUCT]))
    return out
