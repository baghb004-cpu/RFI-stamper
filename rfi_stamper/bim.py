"""GUI-free 3D math + model for the BIM-lite wireframe viewer.

World coordinates: x east, y north, z up.  The camera orbits a target point
(yaw about z, pitch above the horizon) and projects to screen pixels with a
top-left origin and y down — matching the tk canvas the GUI draws on.

Everything here is pure numpy/stdlib and fully offline: no I/O beyond the
minimal OBJ reader, no networking, nothing GUI.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

_EPS = 1e-9


# ------------------------------------------------------------------ camera ---

@dataclass
class Camera:
    """Orbit camera.  yaw/pitch in degrees; dist in world units to target."""
    yaw: float = -35.0
    pitch: float = 22.0
    dist: float = 60.0
    target: tuple = (0.0, 0.0, 0.0)
    fov: float = 42.0
    ortho: bool = False


def _basis(cam: Camera):
    """(eye, right, up, forward) world-space unit vectors for the camera.

    At yaw=0, pitch=0 the eye sits south of the target looking north (+y),
    so +x is right on screen and +z is up.  The analytic right vector
    (cos yaw, sin yaw, 0) stays well-defined even at pitch ±90.
    """
    yr = math.radians(cam.yaw)
    pr = math.radians(cam.pitch)
    cp, sp = math.cos(pr), math.sin(pr)
    cy, sy = math.cos(yr), math.sin(yr)
    fwd = np.array([-sy * cp, cy * cp, -sp])
    right = np.array([cy, sy, 0.0])
    up = np.cross(right, fwd)
    eye = np.asarray(cam.target, dtype=float) - fwd * cam.dist
    return eye, right, up, fwd


basis = _basis      # public alias — raster.py builds its camera space on it


def project_points(pts, cam: Camera, w: int, h: int) -> np.ndarray:
    """Project world points to screen.  Returns (N, 3): x, y (pixels, y down),
    depth (camera-space distance along the view axis).

    NaN/inf-safe: garbage input never produces NaN in the result, and points
    at or behind the camera come back with depth <= 0 so the caller can cull
    them (their x/y are clamped, not trustworthy).
    """
    p = np.asarray(pts, dtype=float)
    if p.size == 0:
        return np.zeros((0, 3))
    p = p.reshape(-1, 3)
    eye, right, up, fwd = _basis(cam)
    v = p - eye
    xc = v @ right
    yc = v @ up
    depth = v @ fwd
    half = math.tan(math.radians(max(cam.fov, 1.0)) * 0.5)
    if cam.ortho:
        k = (h * 0.5) / max(abs(cam.dist) * half, _EPS)
        sx = w * 0.5 + xc * k
        sy = h * 0.5 - yc * k
    else:
        f = (h * 0.5) / half
        d = np.where(depth > _EPS, depth, _EPS)     # never divide by <= 0
        sx = w * 0.5 + xc * f / d
        sy = h * 0.5 - yc * f / d
    out = np.column_stack([sx, sy, depth])
    return np.nan_to_num(out, nan=0.0, posinf=1e9, neginf=-1e9)


# ------------------------------------------------------------------- model ---

@dataclass
class Segment:
    a: tuple
    b: tuple
    color: str = "#8899aa"
    width: float = 1.0
    system: str = ""
    #: pipe radius in world units; 0.0 = draw as a plain line.  Pipewright
    #: sets this (dia_in / 24 -> radius in feet) so the viewer's shaded mode
    #: can extrude the run into an octagonal solid.  Additive: everything
    #: that builds Segments without it keeps today's wireframe behavior.
    radius: float = 0.0


@dataclass
class Face:
    """One filled, flat-shaded polygon for the viewer's shaded mode.
    ``pts`` is 3+ (x, y, z) vertices in drawing order (assumed near-planar);
    ``system`` ties the face to the legend toggles like Segment.system."""
    pts: list
    color: str = "#8f9aa8"
    system: str = ""


@dataclass
class SheetPlane:
    corners: list                   # 4 xyz corners, in drawing order
    label: str = ""
    page_no: int = 0
    color: str = "#3b82f6"


@dataclass
class Model:
    segments: list = field(default_factory=list)
    planes: list = field(default_factory=list)
    systems: list = field(default_factory=list)     # [(system_name, color)]
    faces: list = field(default_factory=list)       # [Face] — shaded mode

    def bounds(self) -> tuple:
        """((minx,miny,minz),(maxx,maxy,maxz)); unit cube at origin if empty."""
        pts = []
        for s in self.segments:
            pts.append(s.a)
            pts.append(s.b)
        for pl in self.planes:
            pts.extend(pl.corners)
        for f in self.faces:
            pts.extend(f.pts)
        if not pts:
            return ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        a = np.asarray(pts, dtype=float)
        mn, mx = a.min(axis=0), a.max(axis=0)
        return (tuple(float(v) for v in mn), tuple(float(v) for v in mx))


# ------------------------------------------------------- face construction ---

def wall_faces(world_segs, wall_height: float, floors: int = 1,
               slab_gap: float = 0.8, color: str = "#9aab9e",
               system: str = "walls") -> list:
    """One quad :class:`Face` per wall per floor, mirroring the z math of
    ``extrude.build_model`` exactly (floor i: z0 = i * (wall_height +
    slab_gap), top = z0 + wall_height).  ``world_segs`` is
    ``[((E, N), (E, N)), ...]``; bim axes are x east / y north / z up."""
    wall_height = float(wall_height)
    if wall_height <= 0:
        raise ValueError(f"wall_height must be positive, got {wall_height}")
    floors = max(1, int(floors))
    slab_gap = float(slab_gap)
    faces: list = []
    for i in range(floors):
        z0 = i * (wall_height + slab_gap)
        z1 = z0 + wall_height
        for a, b in world_segs:
            ea, na = float(a[0]), float(a[1])
            eb, nb = float(b[0]), float(b[1])
            faces.append(Face([(ea, na, z0), (eb, nb, z0),
                               (eb, nb, z1), (ea, na, z1)], color, system))
    return faces


def tube_faces(a, b, radius: float, sides: int = 8, color: str = "#8899aa",
               system: str = "") -> list:
    """Octagonal-prism approximation of a pipe segment: ``sides`` side quads
    plus the two end caps, as :class:`Face` objects around the a->b axis.
    Degenerate input (zero length or radius) returns []."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    axis = b - a
    length = float(np.linalg.norm(axis))
    r = float(radius)
    sides = max(3, int(sides))
    if length < _EPS or r <= 0.0:
        return []
    axis = axis / length
    ref = np.array([0.0, 0.0, 1.0])
    if abs(float(axis @ ref)) > 0.98:            # near-vertical: new reference
        ref = np.array([1.0, 0.0, 0.0])
    u = np.cross(axis, ref)
    u = u / max(float(np.linalg.norm(u)), _EPS)
    v = np.cross(axis, u)
    ring_a, ring_b = [], []
    for k in range(sides):
        th = 2.0 * math.pi * k / sides
        off = u * (r * math.cos(th)) + v * (r * math.sin(th))
        ring_a.append(tuple(float(c) for c in (a + off)))
        ring_b.append(tuple(float(c) for c in (b + off)))
    faces = [Face([ring_a[k], ring_a[(k + 1) % sides],
                   ring_b[(k + 1) % sides], ring_b[k]], color, system)
             for k in range(sides)]
    faces.append(Face(list(ring_a), color, system))
    faces.append(Face(list(ring_b), color, system))
    return faces


def exaggerate_z(pt, z_mid: float, factor: float) -> tuple:
    """Scale a point's z-delta about ``z_mid`` by ``factor`` (x, y pass
    through).  The viewer's slope-exaggeration slider applies this at render
    time only — the model itself is never mutated."""
    return (pt[0], pt[1],
            float(z_mid) + (float(pt[2]) - float(z_mid)) * float(factor))


# ----------------------------------------------------------- demo building ---

# system palette — mid-saturation so lines read on both light and dark canvas
_STRUCT = "#8f9aa8"
_CORE = "#a09080"
_STAIR = "#c98f2e"
_WATER = "#3b82f6"
_SAN = "#22a55e"
_DUCT = "#98a2ad"


def demo_building(floors: int = 3, bays_x: int = 4, bays_y: int = 3) -> Model:
    """Procedural wireframe building: slabs, column grid, core, stair, roof,
    and MEP runs (domestic water, sanitary, one supply-duct double-line)."""
    floors = max(1, int(floors))
    bays_x = max(1, int(bays_x))
    bays_y = max(1, int(bays_y))
    m = Model()
    bx, by, fh = 6.0, 5.0, 3.6                     # bay + storey dimensions
    W, D = bays_x * bx, bays_y * by
    x0, y0 = -W / 2.0, -D / 2.0                    # footprint centred at origin
    top = floors * fh

    def seg(a, b, color=_STRUCT, width=1.0, system="structure"):
        m.segments.append(Segment(tuple(float(v) for v in a),
                                  tuple(float(v) for v in b),
                                  color, width, system))

    def rect(z, xa, ya, xb, yb, color, width=1.0, system="structure"):
        seg((xa, ya, z), (xb, ya, z), color, width, system)
        seg((xb, ya, z), (xb, yb, z), color, width, system)
        seg((xb, yb, z), (xa, yb, z), color, width, system)
        seg((xa, yb, z), (xa, ya, z), color, width, system)

    def box(xa, ya, xb, yb, za, zb, color, width=1.0, system="structure"):
        rect(za, xa, ya, xb, yb, color, width, system)
        rect(zb, xa, ya, xb, yb, color, width, system)
        for (x, y) in ((xa, ya), (xb, ya), (xb, yb), (xa, yb)):
            seg((x, y, za), (x, y, zb), color, width, system)

    # slab outlines, ground through roof
    for lvl in range(floors + 1):
        rect(lvl * fh, x0, y0, x0 + W, y0 + D, _STRUCT, 1.6)

    # column grid, storey by storey
    for i in range(bays_x + 1):
        for j in range(bays_y + 1):
            x, y = x0 + i * bx, y0 + j * by
            for lvl in range(floors):
                seg((x, y, lvl * fh), (x, y, (lvl + 1) * fh), _STRUCT, 1.0)

    # core: one central bay, walls per floor
    ci, cj = bays_x // 2, bays_y // 2
    cxa, cya = x0 + ci * bx, y0 + cj * by
    cxb, cyb = cxa + bx, cya + by
    for lvl in range(floors + 1):
        rect(lvl * fh, cxa, cya, cxb, cyb, _CORE, 1.2)
    for (x, y) in ((cxa, cya), (cxb, cya), (cxb, cyb), (cxa, cyb)):
        for lvl in range(floors):
            seg((x, y, lvl * fh), (x, y, (lvl + 1) * fh), _CORE, 1.2)

    # stair zig-zag inside the core (up, landing, back)
    ys = (cya + cyb) / 2.0
    sxa, sxb = cxa + 0.6, cxb - 0.6
    for lvl in range(floors):
        z = lvl * fh
        seg((sxa, ys, z), (sxb, ys, z + fh / 2), _STAIR, 1.2)
        seg((sxb, ys, z + fh / 2), (sxb, ys - 1.0, z + fh / 2), _STAIR, 1.0)
        seg((sxb, ys - 1.0, z + fh / 2), (sxa, ys - 1.0, z + fh), _STAIR, 1.2)

    # roof: parapet + corner posts, and a rooftop unit above the core
    pz = top + 0.9
    rect(pz, x0, y0, x0 + W, y0 + D, _STRUCT, 1.2)
    for (x, y) in ((x0, y0), (x0 + W, y0), (x0 + W, y0 + D), (x0, y0 + D)):
        seg((x, y, top), (x, y, pz), _STRUCT, 1.0)
    box(cxa + 1.0, cya + 1.0, cxb - 1.0, cyb - 1.0, top, top + 1.6,
        _DUCT, 1.0, "supply duct")

    # ---- MEP along the corridor just south of the core -------------------
    yw = cya - 1.0            # domestic water line
    yn = cya - 2.4            # sanitary line
    grid_x = [x0 + i * bx for i in range(bays_x + 1)]

    for lvl in range(floors):
        zw = lvl * fh + 2.7
        # domestic water: snake between yw and yw-0.7 across the bays + drops
        pts = [(x0 + 0.8, yw, zw)]
        for k, gx in enumerate(grid_x[1:], start=1):
            yy = yw if k % 2 == 0 else yw - 0.7
            xe = min(gx, x0 + W - 0.8)
            pts.append((xe, yy, zw))
        for a, b in zip(pts, pts[1:]):
            seg(a, b, _WATER, 1.2, "domestic water")
        for (px, py, pz2) in pts[1:-1]:
            seg((px, py, pz2), (px, py, pz2 - 1.2), _WATER, 1.0,
                "domestic water")
        # sanitary: opposite-phase snake, shallower
        zs = lvl * fh + 2.3
        pts = [(x0 + 0.8, yn - 0.7, zs)]
        for k, gx in enumerate(grid_x[1:], start=1):
            yy = yn - 0.7 if k % 2 == 0 else yn
            xe = min(gx, x0 + W - 0.8)
            pts.append((xe, yy, zs))
        for a, b in zip(pts, pts[1:]):
            seg(a, b, _SAN, 1.2, "sanitary")

    # water riser (west end) and sanitary riser (east end) tie floors together
    seg((x0 + 0.8, yw, 2.7), (x0 + 0.8, yw, (floors - 1) * fh + 2.7),
        _WATER, 1.4, "domestic water")
    seg((x0 + W - 0.8, yn, 0.5), (x0 + W - 0.8, yn, (floors - 1) * fh + 2.3),
        _SAN, 1.4, "sanitary")

    # one supply-duct run (double line) on the top floor, fed from the RTU
    zd = (floors - 1) * fh + 2.9
    rx = (cxa + cxb) / 2.0
    for off in (-0.3, 0.3):
        seg((rx + off, yn, zd), (rx + off, yn, top), _DUCT, 1.0, "supply duct")
        seg((x0 + 1.2, yn + off, zd), (x0 + W - 1.2, yn + off, zd),
            _DUCT, 1.0, "supply duct")
    for xe in (x0 + 1.2, x0 + W - 1.2):
        seg((xe, yn - 0.3, zd), (xe, yn + 0.3, zd), _DUCT, 1.0, "supply duct")

    m.systems = [("structure", _STRUCT), ("domestic water", _WATER),
                 ("sanitary", _SAN), ("supply duct", _DUCT)]
    return m


def add_sheet_plane(model: Model, label: str, page_no: int, elevation: float,
                    size=(40.0, 28.0)) -> SheetPlane:
    """Horizontal sheet plane at z=elevation centred on the model footprint;
    appended to model.planes and returned."""
    (mnx, mny, _), (mxx, mxy, _) = model.bounds()
    cx, cy = (mnx + mxx) / 2.0, (mny + mxy) / 2.0
    hx, hy = float(size[0]) / 2.0, float(size[1]) / 2.0
    z = float(elevation)
    plane = SheetPlane(
        corners=[(cx - hx, cy - hy, z), (cx + hx, cy - hy, z),
                 (cx + hx, cy + hy, z), (cx - hx, cy + hy, z)],
        label=label, page_no=int(page_no))
    model.planes.append(plane)
    return plane


# --------------------------------------------------------------- OBJ input ---

def load_obj(path: str) -> Model:
    """Minimal offline OBJ reader: `v` vertices, `l` polylines -> segments,
    `f` faces -> outline edges with shared edges deduplicated.  Materials,
    normals and texture coords are ignored.  Raises ValueError when the file
    contains nothing usable."""
    verts: list = []
    seen: set = set()
    order: list = []                # keep first-seen edge order deterministic
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            parts = raw.strip().split()
            if not parts or parts[0].startswith("#"):
                continue
            tag = parts[0]
            if tag == "v" and len(parts) >= 4:
                try:
                    verts.append((float(parts[1]), float(parts[2]),
                                  float(parts[3])))
                except ValueError:
                    continue
            elif tag in ("l", "f") and len(parts) >= 3:
                idx = []
                for tok in parts[1:]:
                    head = tok.split("/")[0]
                    try:
                        i = int(head)
                    except ValueError:
                        continue
                    idx.append(i - 1 if i > 0 else len(verts) + i)
                if len(idx) < 2:
                    continue
                pairs = list(zip(idx, idx[1:]))
                if tag == "f" and len(idx) >= 3:
                    pairs.append((idx[-1], idx[0]))     # close the face loop
                for a, b in pairs:
                    if a == b:
                        continue
                    key = (a, b) if a < b else (b, a)
                    if key not in seen:
                        seen.add(key)
                        order.append(key)
    segs = [Segment(verts[a], verts[b]) for a, b in order
            if 0 <= a < len(verts) and 0 <= b < len(verts)]
    if not segs:
        raise ValueError("no usable geometry in OBJ (need v plus l/f lines)")
    return Model(segments=segs)
