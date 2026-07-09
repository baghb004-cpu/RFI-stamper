"""GUI-free numpy z-buffer rasterizer for the BIM viewer's shaded mode.

Rasterizes :class:`bim.Face` polygons into an RGB frame with a per-pixel
depth buffer, fixing the one correctness hole of the painter's algorithm:
interpenetrating faces (a pipe prism through a wall, two crossing walls)
resolve exactly, per pixel.  Pure numpy + stdlib — no tk, fully offline,
testable headless.

The rules this implementation commits to (and the tests pin):

* Pixel-center sampling at (x + 0.5, y + 0.5); Pineda half-plane edge
  functions decide coverage.
* Inclusive ``>= 0`` on all three edges plus a strict ``>`` depth test in a
  fixed draw order: shared edges are double-covered (never cracked) and a
  depth tie deterministically goes to the first-drawn face.
* Depth interpolated as 1/z under perspective (screen-affine; z itself is
  not), camera-space z under ortho.  Greater always means nearer.
* Near-plane handling by Sutherland-Hodgman clipping in camera space —
  never by clamping (which smears triangles) or whole-triangle rejection
  (which deletes the room the walker is standing in).
* Two-sided fill: wall quads are open, unoriented surfaces, so every
  projected triangle is oriented to positive area and only degenerate
  |area| ~ 0 is culled.  No backface culling, ever.
* Flat 12-bucket lambert shading, computed once per face in world space —
  the exact painter formula.  gui/bim3d imports it from here so toggling
  raster/painter never shifts a color.
"""
from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

from . import bim

#: fixed flat-shading light (unit vector, from the south-west and above) —
#: single source; the painter path in gui/bim3d uses it via lambert_bucket.
LIGHT = np.array([0.45, 0.35, 0.82])
LIGHT = LIGHT / np.linalg.norm(LIGHT)

_AREA_EPS = 1e-9


# ----------------------------------------------------------------- shading ---

def hex_rgb(color) -> tuple:
    """``"#rgb"``/``"#rrggbb"`` -> (r, g, b) ints; rgb tuples pass through."""
    if not isinstance(color, str):
        return tuple(int(v) for v in color[:3])
    s = color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    n = int(s[:6], 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def mix_rgb(c1, c2, t: float) -> tuple:
    """The house color mix: per-channel int(round(lerp)), clamped 0..255.
    Shade in float, quantize once — one rounding for painter and raster."""
    a, b = hex_rgb(c1), hex_rgb(c2)
    return tuple(max(0, min(255, int(round(a[i] + (b[i] - a[i]) * t))))
                 for i in range(3))


def lambert_bucket(normal) -> int:
    """Two-sided flat lambert of a face normal against LIGHT, bucketed
    0..12 (12 = facing the light).  Degenerate normals shade mid-gray."""
    n = np.asarray(normal, dtype=float)
    ln = float(np.linalg.norm(n))
    lam = abs(float(n @ LIGHT)) / ln if ln > 1e-9 else 0.5
    return int(lam * 12 + 0.5)


def shade(color, bucket: int, bg) -> tuple:
    """Flat-shade a face color toward the canvas bg by its lambert bucket
    (0.12 base + up to 0.5 darkness) — the painter's exact formula."""
    return mix_rgb(color, bg, 0.12 + 0.5 * (1.0 - bucket / 12.0))


def face_normal(face) -> np.ndarray:
    """World-space (unnormalized) normal from the first three vertices."""
    p = np.asarray(face.pts[:3], dtype=float)
    return np.cross(p[1] - p[0], p[2] - p[0])


# ------------------------------------------------------------ triangulation ---

def triangulate(faces):
    """Fan-triangulate Face polygons from vertex 0 (exact for the planar
    convex quads/caps every house producer emits).  Returns
    (tris (T, 3, 3) float64 world coords, fidx (T,) int32 face indices)."""
    tris, fidx = [], []
    for i, f in enumerate(faces):
        p = np.asarray(f.pts, dtype=float)
        for k in range(1, len(p) - 1):
            tris.append((p[0], p[k], p[k + 1]))
            fidx.append(i)
    if not tris:
        return np.zeros((0, 3, 3)), np.zeros(0, np.int32)
    return np.asarray(tris), np.asarray(fidx, np.int32)


def _clip_near(tris_c, fidx, znear: float):
    """Clip camera-space triangles against z = znear: all-front pass
    untouched, all-behind drop, stragglers go through single-plane
    Sutherland-Hodgman (a triangle becomes 1 or 2 triangles)."""
    z = tris_c[:, :, 2]
    nfront = (z >= znear).sum(axis=1)
    keep = nfront == 3
    out_t = [tris_c[keep]]
    out_f = [fidx[keep]]
    ct, cf = [], []
    for i in np.nonzero((nfront > 0) & (nfront < 3))[0]:
        tri = tris_c[i]
        poly = []
        for k in range(3):
            a, b = tri[k], tri[(k + 1) % 3]
            a_in = a[2] >= znear
            if a_in:
                poly.append(a)
            if a_in != (b[2] >= znear):
                t = (znear - a[2]) / (b[2] - a[2])
                poly.append(a + (b - a) * t)
        for k in range(1, len(poly) - 1):       # 3 or 4 verts, convex fan
            ct.append((poly[0], poly[k], poly[k + 1]))
            cf.append(fidx[i])
    if ct:
        out_t.append(np.asarray(ct))
        out_f.append(np.asarray(cf, np.int32))
    return np.concatenate(out_t), np.concatenate(out_f)


# ------------------------------------------------------------------- render ---

class Frame(NamedTuple):
    """One rasterized frame.  ``invz`` is the depth attribute per pixel —
    1/z under perspective, -z (camera space) under ortho; in both, greater
    means nearer and empty pixels hold -inf.  ``fid`` is the face index a
    pixel belongs to, -1 for background."""
    rgb: np.ndarray                 # (h, w, 3) uint8
    invz: np.ndarray                # (h, w) float64
    fid: np.ndarray                 # (h, w) int32


def render(faces, cam, w: int, h: int, bg, *, znear: float = 0.05,
           colors=None) -> Frame:
    """Rasterize ``faces`` (bim.Face list) from ``cam`` into a w x h Frame.

    ``bg`` is the background color (hex or rgb).  ``colors`` optionally
    gives one pre-shaded (r, g, b) per face (the GUI passes shaded +
    depth-cued colors); when None each face is flat-shaded here.  The
    viewport math matches bim.project_points exactly, so canvas overlays
    projected the usual way land on the identical pixels.
    """
    w, h = int(w), int(h)
    bg_rgb = hex_rgb(bg)
    img = np.empty((max(h, 1), max(w, 1), 3), np.uint8)
    img[:] = bg_rgb
    invz = np.full(img.shape[:2], -np.inf)
    fid = np.full(img.shape[:2], -1, np.int32)
    frame = Frame(img, invz, fid)
    if not faces or w < 2 or h < 2:
        return frame

    if colors is None:
        colors = [shade(f.color, lambert_bucket(face_normal(f)), bg_rgb)
                  for f in faces]
    colors = np.asarray(colors, np.uint8)

    tris, fidx = triangulate(faces)
    if not len(tris):
        return frame
    eye, right, up, fwd = bim.basis(cam)
    v = tris.reshape(-1, 3) - eye
    camv = np.column_stack([v @ right, v @ up, v @ fwd]).reshape(-1, 3, 3)

    half = math.tan(math.radians(max(cam.fov, 1.0)) * 0.5)
    if cam.ortho:                   # parallel projection: nothing to clip
        k = (h * 0.5) / max(abs(cam.dist) * half, 1e-9)
        sx = w * 0.5 + camv[:, :, 0] * k
        sy = h * 0.5 - camv[:, :, 1] * k
        qz = -camv[:, :, 2]         # camera z is screen-affine under ortho
    else:
        camv, fidx = _clip_near(camv, fidx, znear)
        if not len(camv):
            return frame
        f = (h * 0.5) / half
        z = camv[:, :, 2]           # >= znear after the clip
        sx = w * 0.5 + camv[:, :, 0] * f / z
        sy = h * 0.5 - camv[:, :, 1] * f / z
        qz = 1.0 / z                # 1/z is screen-affine; z itself is not
    sx = np.nan_to_num(sx, nan=0.0, posinf=1e9, neginf=-1e9)
    sy = np.nan_to_num(sy, nan=0.0, posinf=1e9, neginf=-1e9)

    ax, ay, qa = sx[:, 0], sy[:, 0], qz[:, 0]
    bx, by, qb = sx[:, 1], sy[:, 1], qz[:, 1]
    cx, cy, qc = sx[:, 2], sy[:, 2], qz[:, 2]
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    flip = area < 0                 # two-sided: orient to positive area
    bx, cx = np.where(flip, cx, bx), np.where(flip, bx, cx)
    by, cy = np.where(flip, cy, by), np.where(flip, by, cy)
    qb, qc = np.where(flip, qc, qb), np.where(flip, qb, qc)
    area = np.abs(area)

    # edge rows E(x, y) = A x + B y + C; interior of a positive-area
    # triangle has all three E >= 0, and E_bc(a) = area
    A0, B0, C0 = by - cy, cx - bx, bx * cy - cx * by
    A1, B1, C1 = cy - ay, ax - cx, cx * ay - ax * cy
    A2, B2, C2 = ay - by, bx - ax, ax * by - bx * ay
    with np.errstate(divide="ignore", invalid="ignore"):
        # the depth attribute is affine in screen space: q = GA x + GB y + GC
        GA = (A0 * qa + A1 * qb + A2 * qc) / area
        GB = (B0 * qa + B1 * qb + B2 * qc) / area
        GC = (C0 * qa + C1 * qb + C2 * qc) / area

    # integer pixel-center bboxes; cull degenerate/offscreen BEFORE clamping
    x0 = np.ceil(np.minimum(np.minimum(ax, bx), cx) - 0.5).astype(np.int64)
    x1 = np.floor(np.maximum(np.maximum(ax, bx), cx) - 0.5).astype(np.int64)
    y0 = np.ceil(np.minimum(np.minimum(ay, by), cy) - 0.5).astype(np.int64)
    y1 = np.floor(np.maximum(np.maximum(ay, by), cy) - 0.5).astype(np.int64)
    on = ((area > _AREA_EPS) & (x1 >= 0) & (x0 <= w - 1)
          & (y1 >= 0) & (y0 <= h - 1) & (x1 >= x0) & (y1 >= y0))
    x0 = np.clip(x0, 0, w - 1)
    x1 = np.clip(x1, 0, w - 1)
    y0 = np.clip(y0, 0, h - 1)
    y1 = np.clip(y1, 0, h - 1)

    # per-triangle fill, vectorized inner loop: every edge function and the
    # depth plane are affine, so a bbox evaluates as one outer-add of two
    # 1-D arrays.  Draw order is the input order (the z-tie rule).
    for i in np.nonzero(on)[0]:
        pxs = np.arange(x0[i], x1[i] + 1, dtype=float) + 0.5
        pys = np.arange(y0[i], y1[i] + 1, dtype=float) + 0.5
        e0 = (A0[i] * pxs + C0[i]) + (B0[i] * pys)[:, None]
        e1 = (A1[i] * pxs + C1[i]) + (B1[i] * pys)[:, None]
        e2 = (A2[i] * pxs + C2[i]) + (B2[i] * pys)[:, None]
        zi = (GA[i] * pxs + GC[i]) + (GB[i] * pys)[:, None]
        sl = (slice(int(y0[i]), int(y1[i]) + 1),
              slice(int(x0[i]), int(x1[i]) + 1))
        sub = invz[sl]
        win = (e0 >= 0) & (e1 >= 0) & (e2 >= 0) & (zi > sub)
        if not win.any():
            continue
        invz[sl] = np.where(win, zi, sub)
        img[sl] = np.where(win[..., None], colors[fidx[i]], img[sl])
        fid[sl] = np.where(win, fidx[i], fid[sl])
    return frame


def outline_mask(frame: Frame, rel: float = 0.02,
                 soft_from: int = None) -> np.ndarray:
    """Image-space silhouette (h, w) bool: pixels where the face id changes
    AND the depth attribute jumps by more than ``rel`` relative — coplanar
    seams between adjacent faces stay clean, occluding contours and every
    face-vs-background boundary edge crisply.  Four rolls and a where.

    ``soft_from``: face indices >= soft_from (the GUI's ground-grid quads)
    never outline against the background or each other, but real faces
    still outline against them."""
    fid, q = frame.fid, frame.invz
    real = fid >= 0
    if soft_from is not None:
        real = real & (fid < soft_from)
    m = np.zeros(fid.shape, bool)
    for axis in (0, 1):
        f2 = np.roll(fid, 1, axis=axis)
        q2 = np.roll(q, 1, axis=axis)
        r2 = np.roll(real, 1, axis=axis)
        diff = fid != f2
        both = real & r2                        # real-vs-real: gate by depth
        with np.errstate(invalid="ignore"):
            jump = np.abs(q - q2) > rel * np.maximum(np.abs(q), np.abs(q2))
        e = diff & (real | r2) & (~both | jump)
        if axis == 0:                           # np.roll wraps; kill the seam
            e[0, :] = False
        else:
            e[:, 0] = False
        m |= e
    return m
