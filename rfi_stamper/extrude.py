"""Extrude: vector plan linework -> a 3D wireframe building model.

Walls are read straight off a CAD-generated (vector) plan page — every line,
rectangle edge, quad edge and bezier chord from ``page.get_drawings()``
becomes a candidate wall in page points — then georeferenced through the
Fieldstitch calibration (basepoint, plan-north rotation,
:class:`~rfi_stamper.markups.measure.ScaleCal`) and extruded into a
:class:`rfi_stamper.bim.Model` wireframe.  The model lives in the SAME world
frame Fieldstitch exports (Northing/Easting from the basepoint), so layout
pins land inside the real building.

Coordinate conventions:

* page space: viewer page **points**, top-left origin, y **down** (the
  markups / Fieldstitch convention).  ``page.get_drawings()`` returns
  UNROTATED media coordinates on /Rotate pages — the same trap as
  ``get_text("words")`` — so extraction pushes every point through
  ``page.rotation_matrix`` first.
* world space: the Fieldstitch survey frame — the page vector from the
  basepoint (dx, dy) flips to east' = dx, north' = -dy, rotates CCW by
  ``rotation_deg``, scales by ``real_per_pt`` and offsets by ``base_world``
  (N, E).
* bim space: x east, y north, z up — a world segment ((E, N), (E, N)) maps
  to x = E, y = N.

Bezier curves ('c' items) are approximated by the 3 chords of their control
polygon (p0 -> c1 -> c2 -> p1).  That is deliberately coarse: walls are
straight, and curves on plan sheets are mostly door swings and fillets.

Fully offline: fitz + numpy only, no I/O beyond reading the plan PDF.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

import fitz
import numpy as np

from . import bim

_QUANT_PT = 0.5        # endpoint dedupe grid in page points
_COLUMN_MERGE = 0.05   # corner-column merge distance in world units
_WALL_COLOR = "#9aab9e"


@dataclass
class Wall2D:
    """One candidate wall in page space: (x, y) page points, top-left
    origin, y down."""
    a: tuple
    b: tuple


# -------------------------------------------------------------- extraction --

def extract_segments(pdf_path: str, page_no: int = 1, min_len_pt: float = 6.0,
                     max_segments: int = 4000, log=print) -> list[Wall2D]:
    """Pull straight segments out of one vector plan page.

    'l' items give one segment, 're' rectangles four edges, 'qu' quads four
    edges, 'c' beziers three control-polygon chords (see the module note).
    Segments shorter than ``min_len_pt`` are dropped, endpoints are quantized
    to a 0.5 pt grid to dedupe exact duplicates (either direction; the
    returned coordinates stay unquantized), and the result is capped at
    ``max_segments`` (logged when the cap bites).

    Raises ValueError when the page carries no vector linework at all — a
    scanned/raster plan cannot be extruded.
    """
    doc = fitz.open(pdf_path)
    try:
        if not 1 <= int(page_no) <= len(doc):
            raise ValueError(f"page {page_no} outside document "
                             f"(has {len(doc)} page(s))")
        page = doc[int(page_no) - 1]
        raw: list[tuple] = []
        for path in page.get_drawings():
            for item in path.get("items") or ():
                op = item[0]
                if op == "l":
                    p, q = item[1], item[2]
                    raw.append((p.x, p.y, q.x, q.y))
                elif op == "re":
                    r = item[1]
                    c = ((r.x0, r.y0), (r.x1, r.y0),
                         (r.x1, r.y1), (r.x0, r.y1))
                    for a, b in zip(c, c[1:] + c[:1]):
                        raw.append((a[0], a[1], b[0], b[1]))
                elif op == "qu":
                    q = item[1]
                    for a, b in ((q.ul, q.ur), (q.ur, q.lr),
                                 (q.lr, q.ll), (q.ll, q.ul)):
                        raw.append((a.x, a.y, b.x, b.y))
                elif op == "c":
                    # bezier -> 3 control-polygon chords (coarse on purpose)
                    p0, c1, c2, p1 = item[1], item[2], item[3], item[4]
                    for a, b in ((p0, c1), (c1, c2), (c2, p1)):
                        raw.append((a.x, a.y, b.x, b.y))
        if not raw:
            raise ValueError(
                f"no vector linework on page {page_no} of "
                f"{os.path.basename(pdf_path)}: this looks like a scanned/"
                "raster plan. Extrude needs a vector (CAD-generated) PDF — "
                "re-export the plan from CAD, or skip this page.")
        pts = np.asarray(raw, dtype=float)

        # get_drawings coordinates ignore /Rotate: map into viewer space
        rot = page.rotation % 360
        if rot:
            m = page.rotation_matrix
            xs, ys = pts[:, 0::2], pts[:, 1::2]
            pts = pts.copy()
            pts[:, 0::2] = xs * m.a + ys * m.c + m.e
            pts[:, 1::2] = xs * m.b + ys * m.d + m.f

        # drop tick marks / hatch stubs shorter than min_len_pt
        d = np.hypot(pts[:, 2] - pts[:, 0], pts[:, 3] - pts[:, 1])
        pts = pts[d >= float(min_len_pt)]
        if len(pts) == 0:
            raise ValueError(
                f"page {page_no}: all {len(raw)} vector segments are shorter "
                f"than {min_len_pt} pt — nothing wall-sized to extrude")

        # dedupe on a 0.5 pt grid, direction-insensitive, first-seen wins
        q = np.rint(pts / _QUANT_PT).astype(np.int64)
        a, b = q[:, :2], q[:, 2:]
        swap = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0])
                                      & (a[:, 1] > b[:, 1]))
        key = np.where(swap[:, None], np.hstack([b, a]), np.hstack([a, b]))
        _, first = np.unique(key, axis=0, return_index=True)
        pts = pts[np.sort(first)]

        if len(pts) > int(max_segments):
            log(f"  !! {len(pts)} segments after dedupe — "
                f"capped at {max_segments}")
            pts = pts[:int(max_segments)]
        return [Wall2D((float(x0), float(y0)), (float(x1), float(y1)))
                for x0, y0, x1, y1 in pts]
    finally:
        doc.close()


# ------------------------------------------------------------- world math --

def to_world(segs: list[Wall2D], base_page_xy, base_world,
             rotation_deg: float, real_per_pt: float) -> list:
    """Page segments -> ``[((E, N), (E, N)), ...]`` in the Fieldstitch frame.

    Same math as :meth:`fieldstitch.LayoutJob.to_world`: page vector from the
    basepoint (dx, dy) -> east' = dx, north' = -dy (page y runs down),
    rotated CCW by ``rotation_deg``, scaled by ``real_per_pt``, offset by
    ``base_world`` (N, E)."""
    if not segs:
        return []
    pts = np.asarray([(s.a[0], s.a[1], s.b[0], s.b[1]) for s in segs],
                     dtype=float)
    east = pts[:, 0::2] - float(base_page_xy[0])
    north = -(pts[:, 1::2] - float(base_page_xy[1]))
    th = math.radians(float(rotation_deg))
    c, s = math.cos(th), math.sin(th)
    rpp = float(real_per_pt)
    e = float(base_world[1]) + (east * c - north * s) * rpp
    n = float(base_world[0]) + (east * s + north * c) * rpp
    return [((float(e[i, 0]), float(n[i, 0])),
             (float(e[i, 1]), float(n[i, 1]))) for i in range(len(segs))]


# --------------------------------------------------------------- extrusion --

def build_model(world_segs, wall_height: float, floors: int = 1,
                slab_gap: float = 0.8, color: str = _WALL_COLOR) -> bim.Model:
    """Extrude world segments into a wireframe :class:`bim.Model`.

    Per floor i (z0 = i * (wall_height + slab_gap)): each wall gets a bottom
    edge at z0 and a top edge at z0 + wall_height, plus one vertical column
    per wall endpoint — endpoints within ~0.05 world units are merged (by
    grid quantization) so shared corners carry ONE column.  bim world axes
    are x east / y north / z up, so a world segment ((E, N), (E, N)) maps to
    x = E, y = N."""
    wall_height = float(wall_height)
    if wall_height <= 0:
        raise ValueError(f"wall_height must be positive, got {wall_height}")
    floors = max(1, int(floors))
    slab_gap = float(slab_gap)

    walls: list[tuple] = []
    columns: dict[tuple, tuple] = {}         # merge grid key -> first-seen E,N
    for a, b in world_segs:
        ea, na = float(a[0]), float(a[1])
        eb, nb = float(b[0]), float(b[1])
        walls.append(((ea, na), (eb, nb)))
        for e, n in ((ea, na), (eb, nb)):
            key = (round(e / _COLUMN_MERGE), round(n / _COLUMN_MERGE))
            columns.setdefault(key, (e, n))

    model = bim.Model()
    for i in range(floors):
        z0 = i * (wall_height + slab_gap)
        z1 = z0 + wall_height
        for (ea, na), (eb, nb) in walls:
            model.segments.append(bim.Segment((ea, na, z0), (eb, nb, z0),
                                              color, 1.0, "walls"))
            model.segments.append(bim.Segment((ea, na, z1), (eb, nb, z1),
                                              color, 1.0, "walls"))
        for e, n in columns.values():
            model.segments.append(bim.Segment((e, n, z0), (e, n, z1),
                                              color, 1.0, "walls"))
    model.systems = [("walls", color)]
    return model


# ---------------------------------------------------------------- pipeline --

def model_from_plan(pdf_path: str, page_no: int = 1, job=None,
                    wall_height: float = 10.0, floors: int = 1,
                    log=print) -> tuple:
    """Vector plan page -> (bim.Model, stats) in the Fieldstitch world frame.

    ``job`` is a :class:`fieldstitch.LayoutJob` carrying the calibration
    (``cal``), basepoint and plan-north rotation; without a scale the model
    would have no real-world size, so that is an error.  stats =
    ``{"segments", "walls", "height", "floors"}``."""
    if job is None or job.cal is None:
        raise ValueError("set the Fieldstitch scale (and basepoint) first — "
                         "the model needs real-world size")
    segs = extract_segments(pdf_path, page_no=page_no, log=log)
    world = to_world(segs, job.base_page_xy, job.base_world,
                     job.rotation_deg, job.cal.real_per_pt)
    model = build_model(world, wall_height=wall_height, floors=floors)
    stats = {"segments": len(segs), "walls": len(world),
             "height": float(wall_height), "floors": max(1, int(floors))}
    return model, stats
